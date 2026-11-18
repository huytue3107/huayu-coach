"""SQLite engine (WAL) with two session flavours.

Read sessions use a normal deferred BEGIN. Write sessions use BEGIN IMMEDIATE, so a
read-modify-write request (e.g. confirming a proposal) takes the writer lock up front and
cannot hit a stale snapshot. Everything in one request commits or rolls back together.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from anyio import to_thread
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from .config import get_settings

_engine: Engine | None = None
_read_sessions: sessionmaker[Session] | None = None
_write_sessions: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    if is_sqlite and ":memory:" not in url:
        path = url.split("///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {})
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_con, _rec):  # noqa: ANN001
            dbapi_con.isolation_level = None  # we emit BEGIN ourselves
            cur = dbapi_con.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):  # noqa: ANN001
            immediate = conn.get_execution_options().get("sqlite_immediate", False)
            conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")
    return engine


def init_db(url: str | None = None) -> Engine:
    """(Re)initialise the global engine. Tests call this with a temp database."""
    global _engine, _read_sessions, _write_sessions
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url or get_settings().db_url)
    _read_sessions = sessionmaker(bind=_engine, expire_on_commit=False)
    write_engine = _engine.execution_options(sqlite_immediate=True)
    _write_sessions = sessionmaker(bind=write_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def get_db() -> Iterator[Session]:
    """FastAPI dependency for GET handlers (read-only by convention)."""
    engine()
    assert _read_sessions is not None
    with _read_sessions() as session:
        yield session
        session.rollback()


_WRITE_KEY = "huayu.write_session"


def get_wdb(request: Request) -> Session:
    """FastAPI dependency for handlers that write: ONE write session per request, shared by every dependency.

    It is finished by `WriteSessionMiddleware` BEFORE the first byte of the response is sent: committed for a
    status < 400, rolled back otherwise (or on any exception). That ordering matters: a FastAPI dependency with
    `yield` runs its exit code after the response was sent, so a browser following a 303 redirect could read stale
    data, and a failing commit would be reported to the user as success."""
    session = request.scope.get(_WRITE_KEY)
    if session is None:
        engine()
        assert _write_sessions is not None
        session = request.scope[_WRITE_KEY] = _write_sessions()
    return session


def _finish(session: Session, status: int) -> None:
    if status < 400:
        session.commit()
    else:
        session.expunge_all()  # keep loaded attributes readable for the error page (rollback would expire them)
        session.rollback()


def _discard(session: Session) -> None:
    try:
        session.expunge_all()
        session.rollback()
    finally:
        session.close()


class WriteSessionMiddleware:
    """Pure ASGI middleware: commits/rolls back the request's write session when the response starts."""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        done = False

        async def send_wrapper(message):  # noqa: ANN001
            nonlocal done
            if message["type"] == "http.response.start" and not done:
                done = True
                session = scope.get(_WRITE_KEY)
                if session is not None:
                    await to_thread.run_sync(_finish, session, message["status"])  # a failing commit surfaces as a 500
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            if (session := scope.get(_WRITE_KEY)) is not None:
                await to_thread.run_sync(_discard, session)
                scope.pop(_WRITE_KEY, None)
            raise
        finally:
            if (session := scope.pop(_WRITE_KEY, None)) is not None:
                await to_thread.run_sync(_discard, session)


@contextmanager
def write_session() -> Iterator[Session]:
    """For scripts/CLI/tests: same semantics as get_wdb."""
    engine()
    assert _write_sessions is not None
    with _write_sessions() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.expunge_all()
            session.rollback()
            raise


@contextmanager
def read_session() -> Iterator[Session]:
    engine()
    assert _read_sessions is not None
    with _read_sessions() as session:
        yield session
        session.rollback()
