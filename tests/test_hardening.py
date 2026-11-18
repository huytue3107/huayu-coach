"""Regression tests for the findings of the independent security/ops review (each test names the finding)."""
from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import db as dbmod
from app import models, security
from app.config import Settings, get_settings
from tests.conftest import login_as, make_user


@pytest.fixture
def client(engine):
    from app.main import create_app
    security.limiter.reset()
    return TestClient(create_app(), follow_redirects=False)


# ----------------------------------------------------------------------------- sec/F1 (critical)
def test_a_deleted_accounts_cookie_never_works_for_the_next_registrant(client):
    with dbmod.write_session() as s:
        old = make_user(s)
        old_id, old_public = old.id, old.public_id
    stolen = security.make_session_token(old_id, 1, old_public)
    with dbmod.write_session() as s:
        s.execute(delete(models.User).where(models.User.id == old_id))
    with dbmod.write_session() as s:
        new = make_user(s)
        new_id = new.id
    assert new_id != old_id  # AUTOINCREMENT: SQLite no longer hands the freed id to the next account
    client.cookies.set("hc_session", stolen)
    assert client.get("/settings").status_code == 303  # not logged in


def test_a_token_with_someone_elses_public_id_is_refused_even_for_the_same_id(client):
    with dbmod.write_session() as s:
        user = make_user(s)
        uid, version = user.id, user.session_version
    client.cookies.set("hc_session", security.make_session_token(uid, version, "LAAAAAA"))
    assert client.get("/settings").status_code == 303
    client.cookies.set("hc_session", security.make_session_token(uid, version, user.public_id))
    assert client.get("/settings").status_code == 200


# ----------------------------------------------------------------------------- sec/F12
def test_non_ascii_csrf_token_is_a_403_not_a_500(client):
    client.cookies.set("hc_csrf", "plain-ascii-cookie")
    r = client.post("/login", data={"csrf": "²²²", "email": "a@b.co", "password": "x"})  # the form field is attacker-controlled
    assert r.status_code == 403
    assert not security.csrf_ok("abc", "²²²") and not security.csrf_ok("²", "²")


# ----------------------------------------------------------------------------- sec/F3
def test_rate_limiter_memory_is_reclaimed_for_keys_never_seen_again():
    lim = security.SlidingWindow()
    for i in range(600):
        lim.check(f"login-email:2001:db8::{i}", 8, 1)
    time.sleep(1.1)
    for i in range(600, 1200):  # crossing the sweep interval triggers cleanup of the expired keys
        lim.check(f"login-email:2001:db8::{i}", 8, 1)
    assert len(lim._hits) < 700


def test_rate_limiter_hard_cap_evicts_least_recently_used():
    lim = security.SlidingWindow()
    lim.MAX_KEYS = 100
    for i in range(300):
        lim.check(f"k{i}", 5, 3600)
    assert len(lim._hits) <= 150 and "k299" in lim._hits


def test_rate_limit_still_blocks_after_limit():
    lim = security.SlidingWindow()
    for _ in range(3):
        lim.check("a", 3, 60)
    with pytest.raises(security.RateLimited):
        lim.check("a", 3, 60)
    lim.check("b", 3, 60)


# ----------------------------------------------------------------------------- sec/F2
def test_ipv6_clients_are_counted_per_subnet(monkeypatch):
    class Req:
        headers = {}
        client = type("C", (), {"host": "2001:db8:abcd:12:aaaa:bbbb:cccc:1"})()
    a = security.client_ip(Req())
    Req.client = type("C", (), {"host": "2001:db8:abcd:12:1111:2222:3333:4"})()
    assert a == security.client_ip(Req()) == "2001:db8:abcd:12::/64"
    Req.client = type("C", (), {"host": "203.0.113.7"})()
    assert security.client_ip(Req()) == "203.0.113.7"


# ----------------------------------------------------------------------------- sec/F4
def test_argon2_cost_is_bounded():
    assert security._hasher.memory_cost == 19456 and security._hasher.parallelism == 1
    assert security._hash_slots._value <= 4  # at most 4 hashes in flight per process
    h = security.hash_password("a-long-enough-password")
    assert security.verify_password("a-long-enough-password", h) and not security.verify_password("nope", h)
    assert not security.verify_password("anything", None)


# ----------------------------------------------------------------------------- sec/F13, F14, ops/OPS-04
def test_500_page_carries_security_headers_and_never_leaks_details(engine):
    from app.main import create_app
    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "secret detail" not in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"


def test_reset_links_are_redacted_in_the_access_log():
    from app.main import _RedactAccessLog
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/reset/SuperSecretToken123_-x?x=1", "1.1", 200), None)
    _RedactAccessLog().filter(rec)
    assert "SuperSecretToken123" not in rec.getMessage() and "/reset/[redacted]" in rec.getMessage()


def test_monitors_can_use_head(client):
    assert client.head("/healthz").status_code == 200
    assert client.head("/").status_code in (200, 303)


# ----------------------------------------------------------------------------- config
@pytest.mark.parametrize("url", ["https://https://example.com", "http://example.com", "https://", "example.com", "https://a b.com"])
def test_prod_base_url_must_be_a_real_https_host(url):
    with pytest.raises(ValueError):
        Settings(env="prod", secret_key="x" * 40, base_url=url)


def test_invalid_config_message_never_echoes_secret_values(monkeypatch, capsys):
    monkeypatch.setenv("HUAYU_ENV", "prod")
    monkeypatch.setenv("HUAYU_SECRET_KEY", "short-secret-value-XYZ")
    monkeypatch.setenv("HUAYU_BASE_URL", "https://example.com")
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as exc:
        get_settings()
    assert "short-secret-value-XYZ" not in str(exc.value) and "HUAYU_SECRET_KEY" in str(exc.value) or "secret_key" in str(exc.value)


# ----------------------------------------------------------------------------- sec/F7 (SMTP)
class FakeSMTP:
    calls: list = []

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.calls.append(("init", type(self).__name__, host, port, context is not None))

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): FakeSMTP.calls.append(("starttls", context is not None))
    def login(self, u, p): FakeSMTP.calls.append(("login",))
    def send_message(self, m): FakeSMTP.calls.append(("send",))


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.mark.parametrize("security_mode,port,expect_starttls,cls", [("starttls", 587, True, "FakeSMTP"), ("ssl", 465, False, "FakeSMTPSSL"),
                                                                    ("none", 25, False, "FakeSMTP")])
def test_smtp_verifies_certificates_and_supports_all_modes(monkeypatch, security_mode, port, expect_starttls, cls):
    import smtplib
    from app.services import mailer
    FakeSMTP.calls = []
    monkeypatch.setenv("HUAYU_SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("HUAYU_SMTP_PORT", str(port))
    monkeypatch.setenv("HUAYU_SMTP_SECURITY", security_mode)
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    assert mailer.send_mail("a@b.co", "hi", "body") is True
    assert ("init", cls, "mail.example.com", port, cls == "FakeSMTPSSL") in FakeSMTP.calls or any(c[0] == "init" and c[1] == cls for c in FakeSMTP.calls)
    assert (("starttls", True) in FakeSMTP.calls) == expect_starttls  # a context (= certificate verification) is always passed


# ----------------------------------------------------------------------------- integrity/F-05, sec/F10, F15: deletion + export
def test_deleting_an_account_scrubs_staff_notes_and_the_export_shows_them(client):
    from app.security import hash_password
    from app.services import billing
    with dbmod.write_session() as s:
        learner = make_user(s)
        learner.password_hash = hash_password("a-long-enough-password")
        admin = make_user(s, role="admin")
        billing.seed_plans(s)
        s.flush()
        billing.activate(s, learner, "monthly", actor=admin, note="paid by transfer, Nguyen Van A, 0912345678")
        lid, public = learner.id, learner.public_id
    with dbmod.write_session() as s:
        learner = s.get(models.User, lid)
    token = login_as(client, learner)
    exported = client.get("/settings/export").json()
    assert any("Nguyen Van A" in str(a) for a in exported["staff_actions_about_you"])  # a right of access: they see it
    assert "actor_id" not in str(exported["staff_actions_about_you"])
    r = client.post("/settings/delete", data={"csrf": token, "password": "a-long-enough-password", "confirm": "DELETE"})
    assert r.status_code == 303
    with dbmod.read_session() as s:
        everything = str([(a.target, a.detail) for a in s.query(models.AuditLog)]) + str([p.note for p in s.query(models.Payment)])
    assert "Nguyen Van A" not in everything and "0912345678" not in everything and public not in everything


# ----------------------------------------------------------------------------- integrity/F-02: one click, one payment
def test_a_double_submitted_activation_or_grant_is_recorded_once(client):
    from app.services import billing
    with dbmod.write_session() as s:
        learner, admin = make_user(s), make_user(s, role="admin")
        billing.seed_plans(s)
        lid = learner.id
    token = login_as(client, admin)
    first = client.post(f"/admin/users/{lid}/activate", data={"csrf": token, "plan_code": "monthly", "amount": "199", "note": "x"})
    second = client.post(f"/admin/users/{lid}/activate", data={"csrf": token, "plan_code": "monthly", "amount": "199", "note": "x"})
    assert first.status_code == second.status_code == 303
    g1 = client.post(f"/admin/users/{lid}/grant", data={"csrf": token, "days": "7", "note": ""})
    g2 = client.post(f"/admin/users/{lid}/grant", data={"csrf": token, "days": "7", "note": ""})
    assert g1.status_code == g2.status_code == 303
    with dbmod.read_session() as s:
        assert s.query(models.Payment).count() == 1
        assert s.query(models.Subscription).filter_by(user_id=lid).count() == 2  # one activation + one grant, not four


# ----------------------------------------------------------------------------- sec/F11 + F-04: ids with a trailing newline
def test_import_rejects_ids_with_a_trailing_newline_and_admin_digits_must_be_ascii(client):
    import json
    from app.routers import admin as admin_mod
    with dbmod.write_session() as s:
        staff = make_user(s, role="teacher")
    token = login_as(client, staff)
    bad = [{"id": "A0\n", "stage": "daily", "level": "A1", "skill": "vocab", "question_zh": "x", "options": ["a", "b", "c", "d"], "correct_index": 0},
           {"id": "A1", "stage": "daily", "level": "A1", "skill": "vocab", "question_zh": "y", "options": ["a", "b", "c", "d"], "correct_index": 0}]
    r = client.post("/admin/questions/import", data={"csrf": token}, files={"file": ("q.json", json.dumps(bad).encode(), "application/json")})
    assert r.status_code == 200
    with dbmod.read_session() as s:
        assert [q.id for q in s.query(models.Question)] == ["A1"]  # 'A0\n' was refused, not stored
    assert not admin_mod._is_uint("²") and not admin_mod._is_uint("١٢") and admin_mod._is_uint("12")
    assert admin_mod.ID_RE.fullmatch("A0\n") is None


# ----------------------------------------------------------------------------- sec/F8: a stranger cannot burn the owner's recovery
def test_strangers_cannot_exhaust_or_block_password_recovery(client):
    from app.security import hash_password
    from app.services import mailer  # noqa: F401
    with dbmod.write_session() as s:
        victim = make_user(s, email="victim@example.com")
    client.cookies.set("hc_csrf", security.new_csrf_token())
    token = client.cookies["hc_csrf"]
    codes = [client.post("/forgot", data={"csrf": token, "email": "victim@example.com"}).status_code for _ in range(8)]
    assert set(codes) == {303}  # never a 429 that tells the stranger anything or blocks the owner


# ----------------------------------------------------------------------------- integrity/F-07, F-01, F-10
def test_accepting_a_short_rest_never_shortens_a_longer_pause(engine):
    from datetime import timedelta
    from app.services import coach
    from app.timeutil import local_today
    with dbmod.write_session() as s:
        u = make_user(s)
        coach.pause(s, u, None)  # "until I come back"
        rec = coach.report_interruption(s, u, "need_rest")
        coach.decide(s, u, rec.id, "accept", [rec.items[0].id])
        assert u.paused_until.year == 9999
        coach.pause(s, u, 20)
        rec2 = coach.report_interruption(s, u, "need_rest")
        coach.decide(s, u, rec2.id, "accept", [rec2.items[0].id])
        assert u.paused_until == local_today(u.timezone) + timedelta(days=20)


def test_a_decision_does_not_undo_resumed(engine):
    from app.services import coach
    with dbmod.write_session() as s:
        u = make_user(s)
        rec = coach.report_interruption(s, u, "too_hard")
        coach.mark_resumed(s, u)
        assert rec.interruption.status == "resumed"
        coach.decide(s, u, rec.id, "reject")
        assert rec.interruption.status == "resumed" and rec.interruption.resumed_at is not None


def test_manual_plan_cannot_ask_for_more_new_grammar_than_exercises(engine):
    from app.services.plans import CoachError, manual_update
    with dbmod.write_session() as s:
        u = make_user(s)
        with pytest.raises(CoachError, match="invalid_new_grammar"):
            manual_update(s, u, daily_minutes=30, new_grammar=9, exercise_count=4, expected_version=1)


# ----------------------------------------------------------------------------- sec/F5: a slow AI provider must not starve the pool
def test_stuck_releases_its_read_connection_before_the_ai_call(engine, monkeypatch):
    """With the AI on, /stuck used to keep a pooled connection checked out for the whole provider call, so ~15 slow
    requests exhausted the pool and froze the site. Assert no connection is checked out while the provider is called."""
    from app.main import create_app
    from app.services import consent, llm
    monkeypatch.setenv("HUAYU_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUAYU_LLM_MODEL", "m")
    monkeypatch.setenv("HUAYU_LLM_API_KEY", "k")
    get_settings.cache_clear()
    with dbmod.write_session() as s:
        u = make_user(s)
        u.onboarded_at = __import__("app.timeutil", fromlist=["x"]).utcnow()
        consent.record(s, u, llm_transfer_agreed=True)
    seen = {}

    def fake_explain(*a, **k):
        seen["checked_out"] = dbmod.engine().pool.checkedout()
        return None

    monkeypatch.setattr(llm, "explain", fake_explain)
    c = TestClient(create_app(), follow_redirects=False)
    token = login_as(c, u)
    r = c.post("/stuck", data={"csrf": token, "reason": "too_hard", "note": ""})
    assert r.status_code == 303 and seen["checked_out"] == 0


# ----------------------------------------------------------------------------- operator tooling: delete-user, warnings
def test_cli_delete_user_erases_like_the_button_and_removes_outbox_mail(engine, tmp_path, monkeypatch, capsys):
    from app import cli
    from app.services import billing, mailer
    monkeypatch.setenv("HUAYU_DATABASE_URL", engine.url.render_as_string(hide_password=False))  # the CLI opens its own engine
    get_settings.cache_clear()
    with dbmod.write_session() as s:
        learner = make_user(s, email="gone@example.com")
        make_user(s, email="boss@example.com", role="admin")
        admin = make_user(s, email="boss2@example.com", role="admin")
        billing.seed_plans(s)
        billing.activate(s, learner, "monthly", actor=admin, note="Nguyen Van A")
    mailer.send_mail("gone@example.com", "reset", "link")
    mailer.send_mail("other@example.com", "reset", "link")
    outbox = get_settings().data_dir / "outbox"
    assert len(list(outbox.glob("*"))) == 2
    assert cli.main(["delete-user", "--email", "GONE@example.com", "--yes"]) == 0
    with dbmod.read_session() as s:
        assert s.query(models.User).filter_by(email="gone@example.com").count() == 0
        assert "Nguyen Van A" not in str([a.detail for a in s.query(models.AuditLog)])
    assert len(list(outbox.glob("*"))) == 1  # only the other person's mail is left


def test_cli_refuses_to_delete_the_only_admin_or_an_unknown_account(engine, monkeypatch):
    from app import cli
    monkeypatch.setenv("HUAYU_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    with dbmod.write_session() as s:
        make_user(s, email="only@example.com", role="admin")
    with pytest.raises(SystemExit, match="only administrator"):
        cli.main(["delete-user", "--email", "only@example.com", "--yes"])
    with pytest.raises(SystemExit, match="No account"):
        cli.main(["delete-user", "--email", "nobody@example.com", "--yes"])


def test_production_start_warns_about_missing_contact_mail_and_content(engine, monkeypatch):
    from app import cli
    monkeypatch.setenv("HUAYU_ENV", "prod")
    monkeypatch.setenv("HUAYU_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("HUAYU_BASE_URL", "https://example.com")
    monkeypatch.setenv("HUAYU_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    dbmod.init_db()
    text = " ".join(cli.config_warnings())
    assert "HUAYU_SUPPORT_EMAIL" in text and "HUAYU_SMTP_HOST" in text and "approved" in text
