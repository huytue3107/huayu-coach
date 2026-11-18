"""The write session must be finished BEFORE the response starts (see app/db.py)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as dbmod
from app import models
from tests.conftest import login_as, make_user


def _app():
    app = FastAPI()
    app.add_middleware(dbmod.WriteSessionMiddleware)

    @app.post("/ok")
    def ok(db: Session = Depends(dbmod.get_wdb)):
        db.add(models.AuditLog(action="ok"))
        return {"ok": True}

    @app.post("/denied")
    def denied(db: Session = Depends(dbmod.get_wdb)):
        db.add(models.AuditLog(action="denied"))
        raise HTTPException(403, "nope")

    @app.post("/boom")
    def boom(db: Session = Depends(dbmod.get_wdb)):
        db.add(models.AuditLog(action="boom"))
        raise RuntimeError("bug")

    @app.post("/dup")
    def dup(db: Session = Depends(dbmod.get_wdb)):
        db.add(models.Question(id="QDUP", stage="daily", level="A1", skill="vocab", question_zh="x", options=["a", "b", "c", "d"], correct_index=0))
        db.add(models.Question(id="QDUP", stage="daily", level="A1", skill="vocab", question_zh="x", options=["a", "b", "c", "d"], correct_index=0))
        return {"ok": True}

    return app


def count(action):
    with dbmod.read_session() as s:
        return s.scalar(select(func.count()).select_from(models.AuditLog).where(models.AuditLog.action == action))


def test_data_is_committed_before_the_response_starts(engine):
    """Drive the ASGI app by hand and look at the database at the exact moment the response headers go out."""
    seen = {}

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                seen["rows_at_response_start"] = count("ok")

        scope = {"type": "http", "method": "POST", "path": "/ok", "headers": [], "query_string": b"", "http_version": "1.1",
                 "scheme": "http", "server": ("t", 80), "client": ("c", 1), "root_path": "", "raw_path": b"/ok"}
        await _app()(scope, receive, send)

    asyncio.run(run())
    assert seen["rows_at_response_start"] == 1  # a browser following a redirect would already see the row


def test_error_statuses_and_exceptions_roll_back(engine):
    c = TestClient(_app(), raise_server_exceptions=False)
    assert c.post("/denied").status_code == 403
    assert c.post("/boom").status_code == 500
    assert count("denied") == 0 and count("boom") == 0
    assert c.post("/ok").status_code == 200 and count("ok") == 1


def test_a_failing_commit_is_a_500_not_a_silent_success(engine):
    r = TestClient(_app(), raise_server_exceptions=False).post("/dup")
    assert r.status_code == 500  # the old after-response commit would have shown the user success


def test_error_page_still_renders_for_a_logged_in_user_after_a_failed_write(engine):
    """Regression: CSRF 403 after the user was loaded used to crash the error page (DetachedInstanceError -> 500)."""
    from app.main import create_app
    with dbmod.write_session() as s:
        user = make_user(s)
    c = TestClient(create_app(), follow_redirects=False)
    login_as(c, user)
    r = c.post("/logout", data={"csrf": "forged"})
    assert r.status_code == 403 and "<html" in r.text
