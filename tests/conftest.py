from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app import models
from app.config import get_settings
from app.services.plans import create_default_plan

_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HUAYU_ENV", "test")
    monkeypatch.setenv("HUAYU_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine(tmp_path):
    eng = dbmod.init_db(f"sqlite:///{tmp_path}/test.db")
    models.Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with dbmod.write_session() as s:
        yield s


def make_user(db, *, daily_minutes=30, email=None, role="learner") -> models.User:
    n = next(_counter)
    user = models.User(email=email or f"u{n}@example.com", password_hash="x", role=role)
    db.add(user)
    db.flush()
    create_default_plan(db, user, daily_minutes)
    db.flush()
    return user


def add_attempt(db, user, qid, *, correct, skill="grammar", topic=None, when=None, stage="daily"):
    if db.get(models.Question, qid) is None:
        db.add(models.Question(id=qid, stage="daily", level="A1", skill=skill, topic=topic, question_zh="q",
                               options=["a", "b", "c", "d"], correct_index=0))
        db.flush()
    db.add(models.QuizAttempt(user_id=user.id, question_id=qid, stage=stage, selected_text="a", is_correct=correct,
                              skill=skill, topic=topic, created_at=when or datetime.now(timezone.utc)))
    db.flush()


# ----------------------------------------------------------------------------- web helpers (shared by all web tests)
@pytest.fixture
def seeded(engine):
    """Loads the 50 seed questions + billing plans."""
    from app.services.seed import seed_all
    with dbmod.write_session() as s:
        seed_all(s)


@pytest.fixture
def web(engine):
    """TestClient on a fresh app + temp database. Redirects are NOT followed; assert on them explicitly."""
    from app.main import create_app
    from app.security import limiter
    limiter.reset()
    return TestClient(create_app(), follow_redirects=False)


def login_as(client: TestClient, user: models.User) -> str:
    """Give the client a valid session + CSRF cookie for `user`. Returns the CSRF token to put in `csrf` form fields."""
    from app.security import make_session_token, new_csrf_token
    token = new_csrf_token()
    client.cookies.set("hc_session", make_session_token(user.id, user.session_version, user.public_id))
    client.cookies.set("hc_csrf", token)
    return token


def onboarded_user(db, *, daily_minutes=30, level="A1", **kw) -> models.User:
    """A learner who finished onboarding and the placement test (so every member page is reachable)."""
    from app.timeutil import utcnow
    user = make_user(db, daily_minutes=daily_minutes, **kw)
    user.level, user.onboarded_at, user.pretest_done_at = level, utcnow(), utcnow()
    db.flush()
    return user
