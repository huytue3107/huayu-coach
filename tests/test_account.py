"""Slice A (account): onboarding, settings, consent, data export and account deletion."""
from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES
from app.domain.levels import PLAN_PRESETS
from app.security import hash_password, hash_token, make_session_token, verify_password
from app.services import billing, coach, consent
from app.timeutil import local_today, utcnow
from tests.conftest import add_attempt, login_as, make_user

PASSWORD = "correct horse battery"
NEW_PASSWORD = "an even better secret"


@pytest.fixture(autouse=True)
def _only_my_routers(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "auth,account")


# ----------------------------------------------------------------------------- helpers
def new_learner(email="new@example.com", **kw) -> models.User:
    """A learner who has just registered: no plan, not onboarded. English unless told otherwise (a logged-in
    person's saved language beats the Accept-Language header, so the tests that read the page set it explicitly)."""
    kw.setdefault("locale", "en")
    with dbmod.write_session() as db:
        user = models.User(email=email, password_hash=hash_password(PASSWORD), **kw)
        db.add(user)
        db.flush()
        billing.start_trial(db, user)
        return user


def ready_user(email="ana@example.com", locale="en", **kw) -> models.User:
    """A learner with a plan who finished onboarding (the helper in conftest hashes nothing, so set a real hash)."""
    with dbmod.write_session() as db:
        user = make_user(db, email=email, **kw)
        user.password_hash = hash_password(PASSWORD)
        user.onboarded_at, user.level, user.locale = utcnow(), "A1", locale
        db.flush()
        return user


def fresh(user_id: int) -> models.User:
    with dbmod.write_session() as db:  # (a read session expires what it returns when it closes)
        return db.get(models.User, user_id)


def count(model, **filters) -> int:
    with dbmod.read_session() as db:
        return db.scalar(select(func.count()).select_from(model).filter_by(**filters))


def rows(model, **filters):
    with dbmod.write_session() as db:
        return list(db.scalars(select(model).filter_by(**filters).order_by(model.id)))


def onboard_form(token, **over):
    return {"csrf": token, "action": "finish", "display_name": "Ana", "daily_minutes": "20", "consent_data": "1", **over}


def en(client):
    client.headers["accept-language"] = "en"
    return client


def second_client(app_client) -> TestClient:
    return TestClient(app_client.app, follow_redirects=False)


# ----------------------------------------------------------------------------- onboarding
def test_onboarding_creates_plan_consent_and_marks_onboarded(web):
    user = new_learner()
    token = login_as(web, user)
    page = web.get("/onboarding")
    assert page.status_code == 200 and 'name="consent_data"' in page.text and 'name="consent_llm"' in page.text
    assert not re.search(r'name="consent_llm"[^>]*\bchecked\b', page.text)

    r = web.post("/onboarding", data=onboard_form(token, display_name="  Ana   Nguyen ", daily_minutes="15"))
    assert r.status_code == 303 and r.headers["location"] == "/placement"  # level is not known yet

    after = fresh(user.id)
    assert after.onboarded_at is not None and after.display_name == "Ana Nguyen"
    (plan,) = rows(models.StudyPlan, user_id=user.id)
    assert (plan.daily_minutes, plan.new_grammar, plan.exercise_count, plan.version) == (15, *PLAN_PRESETS[15], 1)
    (change,) = rows(models.PlanChange, user_id=user.id)
    assert change.source == "default" and change.new_version == 1
    (c,) = rows(models.Consent, user_id=user.id)
    assert c.llm_transfer_agreed is False and c.version == consent.CONSENT_VERSION


def test_onboarding_records_the_optional_ai_consent_separately(web):
    user = new_learner()
    token = login_as(web, user)
    r = web.post("/onboarding", data=onboard_form(token, consent_llm="1"))
    assert r.status_code == 303
    (c,) = rows(models.Consent, user_id=user.id)
    assert c.llm_transfer_agreed is True


def test_onboarding_sends_a_learner_with_a_level_to_today(web):
    user = new_learner(level="A2")
    token = login_as(web, user)
    assert web.post("/onboarding", data=onboard_form(token)).headers["location"] == "/today"


def test_onboarding_cannot_be_skipped_without_the_required_consent(web):
    user = new_learner()
    token = login_as(en(web), user)
    form = onboard_form(token)
    del form["consent_data"]
    r = web.post("/onboarding", data=form)
    assert r.status_code == 400 and i18n.t("en", "account.err.consent_required") in r.text
    # what the person typed is kept, and nothing was saved
    assert 'value="Ana"' in r.text and re.search(r'name="daily_minutes" value="20" checked', r.text)
    r = web.post("/onboarding", data=onboard_form(token, consent_data="0"))
    assert r.status_code == 400
    after = fresh(user.id)
    assert after.onboarded_at is None and after.display_name is None
    assert count(models.StudyPlan) == 0 and count(models.Consent) == 0 and count(models.PlanChange) == 0


@pytest.mark.parametrize("minutes", ["", "7", "0", "-30", "abc", "30.5", "999999999999999999999", "20; DROP TABLE users"])
def test_onboarding_rejects_unknown_daily_minutes(web, minutes):
    user = new_learner()
    token = login_as(en(web), user)
    r = web.post("/onboarding", data=onboard_form(token, daily_minutes=minutes))
    assert r.status_code == 400 and i18n.t("en", "account.err.minutes_invalid") in r.text
    assert count(models.StudyPlan) == 0 and fresh(user.id).onboarded_at is None


def test_onboarding_name_is_optional_trimmed_and_limited(web):
    user = new_learner()
    token = login_as(en(web), user)
    r = web.post("/onboarding", data=onboard_form(token, display_name="x" * 61))
    assert r.status_code == 400 and i18n.t("en", "account.err.name_long", max_name=60) in r.text
    assert fresh(user.id).onboarded_at is None
    r = web.post("/onboarding", data=onboard_form(token, display_name=" \t​\x07  "))  # nothing printable left
    assert r.status_code == 303 and fresh(user.id).display_name is None


def test_onboarding_does_not_create_a_second_plan(web):
    with dbmod.write_session() as db:
        user = make_user(db, daily_minutes=45)
        user.password_hash = hash_password(PASSWORD)
        db.flush()
    token = login_as(web, user)
    assert 'name="daily_minutes"' not in web.get("/onboarding").text  # the question is not asked twice
    r = web.post("/onboarding", data=onboard_form(token, daily_minutes="10"))
    assert r.status_code == 303
    (plan,) = rows(models.StudyPlan, user_id=user.id)
    assert plan.daily_minutes == 45 and count(models.PlanChange, user_id=user.id) == 1
    assert fresh(user.id).onboarded_at is not None


def test_onboarding_language_step_switches_the_page_language(web):
    user = new_learner(locale="vi")
    token = login_as(web, user)
    r = web.post("/onboarding", data={"csrf": token, "action": "language", "locale": "en"})
    assert r.status_code == 303 and r.headers["location"] == "/onboarding" and "hc_lang=en" in r.headers["set-cookie"]
    assert fresh(user.id).locale == "en" and fresh(user.id).onboarded_at is None
    assert i18n.t("en", "account.onboarding.title").replace("'", "&#39;") in web.get("/onboarding").text
    web.post("/onboarding", data={"csrf": token, "action": "language", "locale": "klingon"})
    assert fresh(user.id).locale == "en"  # an unknown language changes nothing


def test_onboarded_learner_is_sent_on_and_a_second_submit_adds_nothing(web):
    user = ready_user()
    token = login_as(web, user)
    assert web.get("/onboarding").headers["location"] == "/today"
    r = web.post("/onboarding", data=onboard_form(token, consent_llm="1", display_name="Changed"))
    assert r.status_code == 303 and r.headers["location"] == "/today"
    assert count(models.Consent, user_id=user.id) == 0 and fresh(user.id).display_name is None


@pytest.mark.parametrize("role", ["admin", "teacher"])
def test_staff_do_not_onboard(web, role):
    with dbmod.write_session() as db:
        staff = models.User(email=f"{role}@example.com", password_hash=hash_password(PASSWORD), role=role)
        db.add(staff)
        db.flush()
    token = login_as(web, staff)
    assert web.get("/onboarding").headers["location"] == "/admin"
    r = web.post("/onboarding", data=onboard_form(token))
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert count(models.StudyPlan) == 0 and count(models.Consent) == 0 and fresh(staff.id).onboarded_at is None


def test_onboarding_needs_login_and_csrf(web):
    r = web.get("/onboarding")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/onboarding"
    user = new_learner()
    login_as(web, user)
    assert web.post("/onboarding", data={"action": "finish", "daily_minutes": "20", "consent_data": "1"}).status_code == 403
    assert web.post("/onboarding", data=onboard_form("forged")).status_code == 403
    assert count(models.StudyPlan) == 0 and fresh(user.id).onboarded_at is None


def test_registration_flows_into_onboarding_end_to_end(web):
    web.get("/register")
    token = web.cookies.get("hc_csrf")
    r = web.post("/register", data={"csrf": token, "email": "flow@example.com", "password": PASSWORD})
    assert r.headers["location"] == "/onboarding"
    assert web.get("/onboarding").status_code == 200
    r = web.post("/onboarding", data=onboard_form(token, daily_minutes="30"))
    assert r.headers["location"] == "/placement"
    with dbmod.write_session() as db:
        u = db.scalar(select(models.User).where(models.User.email == "flow@example.com"))
        assert u.onboarded_at and u.plan.daily_minutes == 30


# ----------------------------------------------------------------------------- settings: profile
def test_settings_page_and_links(web):
    user = ready_user()
    login_as(web, user)
    page = web.get("/settings")
    assert page.status_code == 200
    for link in ('class="btn btn-secondary" href="/plan"', 'class="btn btn-secondary" href="/billing"', 'href="/settings/export"', 'action="/settings/delete"',
                 'action="/settings/consent"'):
        assert link in page.text
    assert "Asia/Taipei" in page.text and 'name="mode"' not in page.text  # no control that does nothing
    # a teacher has no plan and no billing page of their own
    with dbmod.write_session() as db:
        teacher = models.User(email="t@example.com", password_hash=hash_password(PASSWORD), role="teacher")
        db.add(teacher)
        db.flush()
    other = second_client(web)
    login_as(other, teacher)
    assert 'id="s-links"' not in other.get("/settings").text and 'id="s-links"' in web.get("/settings").text


def test_settings_needs_login(web):
    r = web.get("/settings")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings"
    assert web.get("/settings/export").status_code == 303


def test_settings_profile_changes_are_saved(web):
    user = ready_user()
    token = login_as(web, user)
    data = {"csrf": token, "section": "profile", "locale": "zh-TW", "display_name": "  Ana \n Nguyen ", "timezone": "Asia/Tokyo"}
    r = web.post("/settings", data=data)
    assert r.status_code == 303 and r.headers["location"] == "/settings" and "hc_lang=zh-TW" in r.headers["set-cookie"]
    u = fresh(user.id)
    assert (u.locale, u.display_name, u.timezone) == ("zh-TW", "Ana Nguyen", "Asia/Tokyo")
    page = web.get("/settings")  # now in the new language, with the "saved" flash
    assert i18n.t("zh-TW", "common.flash.saved") in page.text
    assert '<option value="Asia/Tokyo" selected>' in page.text

    data.update(display_name="   ")  # clearing the name is allowed
    web.post("/settings", data=data)
    assert fresh(user.id).display_name is None


@pytest.mark.parametrize("field,value,key", [
    ("locale", "fr", "account.err.locale_invalid"), ("locale", "", "account.err.locale_invalid"),
    ("timezone", "Mars/Olympus", "account.err.timezone_invalid"), ("timezone", "", "account.err.timezone_invalid"),
    ("display_name", "n" * 61, "account.err.name_long"),
])
def test_settings_profile_validation_changes_nothing(web, field, value, key):
    user = ready_user()
    token = login_as(en(web), user)
    data = {"csrf": token, "section": "profile", "locale": "en", "display_name": "Ana", "timezone": "Asia/Taipei", "mode": "balanced"}
    data[field] = value
    r = web.post("/settings", data=data)
    assert r.status_code == 400 and i18n.t("en", key, max_name=60) in r.text and 'role="alert"' in r.text
    u = fresh(user.id)
    assert (u.locale, u.display_name, u.timezone, u.mode) == ("en", None, "Asia/Taipei", "balanced")


def test_a_timezone_set_elsewhere_stays_selectable_but_cannot_be_picked_by_hand(web):
    user = ready_user(email="paris@example.com")
    with dbmod.write_session() as db:
        db.get(models.User, user.id).timezone = "Europe/Paris"
    token = login_as(web, user)
    assert '<option value="Europe/Paris" selected>' in web.get("/settings").text
    base = {"csrf": token, "section": "profile", "locale": "en", "display_name": "", "mode": "balanced"}
    assert web.post("/settings", data={**base, "timezone": "Europe/Paris"}).status_code == 303  # unchanged: fine
    assert web.post("/settings", data={**base, "timezone": "Europe/London"}).status_code == 400


def test_settings_unknown_section_and_missing_csrf(web):
    user = ready_user()
    token = login_as(web, user)
    r = web.post("/settings", data={"csrf": token, "section": "role", "mode": "active"})
    assert r.status_code == 303 and fresh(user.id).mode == "balanced"
    assert web.post("/settings", data={"section": "profile", "locale": "en", "display_name": "x", "timezone": "UTC", "mode": "active"}).status_code == 403
    assert fresh(user.id).locale == "en"


def test_role_and_other_columns_cannot_be_set_through_the_forms(web):
    user = ready_user()
    token = login_as(web, user)
    web.post("/settings", data={"csrf": token, "section": "profile", "locale": "vi", "display_name": "", "timezone": "UTC",
                                "mode": "balanced", "role": "admin", "email": "evil@example.com", "level": "A2",
                                "session_version": "99", "onboarded_at": "", "public_id": "LHACKED"})
    u = fresh(user.id)
    assert (u.role, u.email, u.level, u.session_version) == ("learner", "ana@example.com", "A1", 1) and u.public_id != "LHACKED"


# ----------------------------------------------------------------------------- settings: password
def pw_form(token, current=PASSWORD, new=NEW_PASSWORD, new2=None):
    return {"csrf": token, "section": "password", "current_password": current, "new_password": new,
            "new_password2": new if new2 is None else new2}


def test_password_change_keeps_this_device_and_logs_out_the_others(web):
    user = ready_user()
    token = login_as(web, user)
    other = second_client(web)
    login_as(other, user)
    old_cookie = make_session_token(user.id, 1, user.public_id)
    with dbmod.write_session() as db:  # a reset link that is still waiting in the inbox
        db.add(models.PasswordReset(user_id=user.id, token_hash=hash_token("pending"), expires_at=utcnow() + timedelta(hours=1)))

    r = web.post("/settings", data=pw_form(token))
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    u = fresh(user.id)
    assert u.session_version == 2 and verify_password(NEW_PASSWORD, u.password_hash) and not verify_password(PASSWORD, u.password_hash)
    new_cookie = re.search(r"hc_session=([^;]+)", r.headers["set-cookie"]).group(1)  # the session is re-issued ...
    assert new_cookie != old_cookie
    this_device = second_client(web)
    this_device.cookies.set("hc_session", new_cookie)
    assert this_device.get("/settings").status_code == 200  # ... so this device stays logged in
    gone = other.get("/settings")  # every device still holding the old cookie is logged out
    assert gone.status_code == 303 and gone.headers["location"].startswith("/login")
    assert all(p.used_at is not None for p in rows(models.PasswordReset, user_id=user.id))


@pytest.mark.parametrize("override,field,key", [
    ({"current": "wrong password"}, "current_password", "account.err.current_password_wrong"),
    ({"current": ""}, "current_password", "account.err.current_password_wrong"),
    ({"new": "short"}, "new_password", "auth.err.password_short"),
    ({"new": "password123"}, "new_password", "auth.err.password_common"),
    ({"new": "ana@example.com"}, "new_password", "auth.err.password_same_as_email"),
    ({"new": PASSWORD}, "new_password", "account.err.password_same"),
    ({"new2": "something else entirely"}, "new_password2", "auth.err.password_mismatch"),
])
def test_password_change_validation(web, override, field, key):
    user = ready_user()
    token = login_as(en(web), user)
    r = web.post("/settings", data=pw_form(token, **override))
    assert r.status_code == 400 and i18n.t("en", key, min=8, max_password=128) in r.text
    u = fresh(user.id)
    assert u.session_version == 1 and verify_password(PASSWORD, u.password_hash)


def test_password_change_needs_csrf_and_is_rate_limited(web):
    user = ready_user()
    token = login_as(web, user)
    form = pw_form(token)
    del form["csrf"]
    assert web.post("/settings", data=form).status_code == 403
    for _ in range(10):
        assert web.post("/settings", data=pw_form(token, current="wrong password")).status_code == 400
    assert web.post("/settings", data=pw_form(token)).status_code == 429  # even the right password waits
    assert verify_password(PASSWORD, fresh(user.id).password_hash)


# ----------------------------------------------------------------------------- settings: consent
def test_consent_toggle_appends_rows_and_never_edits_them(web):
    user = ready_user()
    token = login_as(en(web), user)
    assert "Off: nothing is sent" in web.get("/settings").text

    r = web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert [c.llm_transfer_agreed for c in rows(models.Consent, user_id=user.id)] == [True]
    assert i18n.t("en", "account.flash.consent_on") in web.get("/settings").text
    assert "On: anonymous study facts" in web.get("/settings").text and 'value="0"' in web.get("/settings").text

    web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": "1"})  # same state again: nothing new
    assert count(models.Consent, user_id=user.id) == 1
    web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": "0"})
    web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": "1"})
    history = [c.llm_transfer_agreed for c in rows(models.Consent, user_id=user.id)]
    assert history == [True, False, True]  # append-only history, newest last
    with dbmod.read_session() as db:
        assert consent.llm_allowed(db, fresh(user.id)) is True


def test_consent_toggle_rejects_odd_values_and_missing_csrf(web):
    user = ready_user()
    token = login_as(web, user)
    for value in ("", "yes", "2", "true"):
        r = web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": value})
        assert r.status_code == 303
    assert count(models.Consent, user_id=user.id) == 0
    assert web.post("/settings/consent", data={"llm_transfer_agreed": "1"}).status_code == 403
    assert count(models.Consent, user_id=user.id) == 0


# ----------------------------------------------------------------------------- export + delete: a learner with data everywhere
def fill(db, user, tag: str) -> None:
    """One row (at least) in every learner-owned table."""
    consent.record(db, user, True)
    add_attempt(db, user, f"QX-{tag}", correct=False, topic="了")
    db.add(models.StudyTask(user_id=user.id, task_date=local_today(), day_no=1, question_ids=[f"QX-{tag}"], planned_minutes=30,
                            plan_version=1))
    rec = coach.report_interruption(db, user, "too_hard", f"note-{tag}")
    db.add(models.Feedback(user_id=user.id, recommendation_id=rec.id, clarity=4, helpfulness=5, pressure=2, comment=f"fb-{tag}"))
    sub = billing.start_trial(db, user)
    db.add(models.Payment(user_id=user.id, subscription_id=sub.id, amount_twd=199, note=f"bank transfer for {tag}"))
    db.add(models.PasswordReset(user_id=user.id, token_hash=hash_token(f"reset-{tag}"), expires_at=utcnow() + timedelta(hours=1)))
    db.flush()


LEARNER_TABLES = (models.Consent, models.StudyPlan, models.PlanChange, models.QuizAttempt, models.StudyTask, models.Interruption,
                  models.Recommendation, models.Feedback, models.Subscription, models.PasswordReset)


@pytest.fixture
def two_learners(web):
    with dbmod.write_session() as db:
        a = make_user(db, email="ana@example.com")
        b = make_user(db, email="bao@example.com")
        for u in (a, b):
            u.password_hash = hash_password(PASSWORD)
            u.onboarded_at, u.level, u.display_name = utcnow(), "A1", "Ana" if u is a else "Bao"
        fill(db, a, "ana")
        fill(db, b, "bao")
        db.flush()
        return a, b


def test_export_contains_everything_and_no_secrets(web, two_learners):
    a, b = two_learners
    login_as(web, a)
    r = web.get("/settings/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    disposition = r.headers["content-disposition"]
    assert disposition == f'attachment; filename="huayu-coach-{a.public_id}.json"' and "ana@example.com" not in disposition
    data = json.loads(r.text)
    assert set(data) == {"export_version", "exported_at", "profile", "consents", "plan", "plan_changes", "quiz_attempts",
                         "study_tasks", "interruptions", "recommendations", "feedback", "subscriptions", "payments", "staff_actions_about_you"}
    assert data["profile"]["public_id"] == a.public_id and data["profile"]["email"] == "ana@example.com"
    assert data["profile"]["display_name"] == "Ana" and data["profile"]["created_at"].endswith("+00:00")
    assert data["plan"]["daily_minutes"] == 30 and len(data["plan_changes"]) == 1
    assert len(data["consents"]) == 1 and data["consents"][0]["llm_transfer_agreed"] is True
    assert [q["question_id"] for q in data["quiz_attempts"]] == ["QX-ana"]
    assert len(data["study_tasks"]) == 1 and data["study_tasks"][0]["question_ids"] == ["QX-ana"]
    assert data["interruptions"][0]["self_report"] == "note-ana"
    (rec,) = data["recommendations"]
    assert rec["items"] and {"action_code", "params", "evidence", "status"} <= set(rec["items"][0])
    assert data["feedback"][0]["comment"] == "fb-ana" and len(data["subscriptions"]) == 1
    assert data["payments"][0]["note"] == "bank transfer for ana" and data["payments"][0]["amount_twd"] == 199

    # nothing secret, nothing of the other learner
    for forbidden in ("password", "argon2", "session_version", "token_hash", "bao@example.com", b.public_id, "QX-bao", "note-bao",
                      "fb-bao", "bank transfer for bao"):
        assert forbidden not in r.text, forbidden
    assert "user_id" not in r.text


def test_export_ignores_attempts_to_name_another_user(web, two_learners):
    a, b = two_learners
    login_as(web, a)
    r = web.get("/settings/export", params={"user_id": b.id, "public_id": b.public_id, "email": "bao@example.com"})
    assert json.loads(r.text)["profile"]["public_id"] == a.public_id and "bao@example.com" not in r.text


def test_export_for_a_brand_new_learner_is_valid_and_mostly_empty(web):
    user = new_learner()
    login_as(web, user)
    data = json.loads(web.get("/settings/export").text)
    assert data["plan"] is None and data["quiz_attempts"] == [] and data["consents"] == [] and len(data["subscriptions"]) == 1


def test_export_is_rate_limited(web):
    user = ready_user()
    login_as(web, user)
    for _ in range(10):
        assert web.get("/settings/export").status_code == 200
    r = web.get("/settings/export")
    assert r.status_code == 429 and "content-disposition" not in r.headers


def delete_form(token, password=PASSWORD, confirm="DELETE", **extra):
    return {"csrf": token, "password": password, "confirm": confirm, **extra}


def test_delete_removes_every_row_of_the_learner_and_logs_it(web, two_learners):
    a, b = two_learners
    token = login_as(web, a)
    before_b = {m.__tablename__: count(m, user_id=b.id) for m in LEARNER_TABLES}
    assert all(n >= 1 for n in before_b.values()), before_b  # the fixture really filled every table
    assert all(count(m, user_id=a.id) >= 1 for m in LEARNER_TABLES)

    r = web.post("/settings/delete", data=delete_form(token, confirm=" delete "))  # any case, stray spaces forgiven
    assert r.status_code == 303 and r.headers["location"] == "/"
    (cleared,) = [c for c in r.headers.get_list("set-cookie") if c.startswith("hc_session=")]
    assert "Max-Age=0" in cleared  # the session cookie is deleted

    assert count(models.User, id=a.id) == 0
    for model in LEARNER_TABLES:
        assert count(model, user_id=a.id) == 0, model.__tablename__
    with dbmod.read_session() as db:
        assert db.scalar(select(func.count()).select_from(models.SuggestionItem).join(models.Recommendation).where(
            models.Recommendation.user_id == a.id)) == 0
    # the other learner lost nothing
    assert {m.__tablename__: count(m, user_id=b.id) for m in LEARNER_TABLES} == before_b and count(models.User, id=b.id) == 1

    (log,) = rows(models.AuditLog, action="account_deleted")
    # the audit row names no one: the anonymous code is replaced by a marker so the log cannot be tied back to the person
    assert log.actor_id is None and log.target == "(deleted account)" and "ana@example.com" not in json.dumps(log.detail)
    assert a.public_id not in json.dumps([r.target for r in rows(models.AuditLog)])
    # the payment is kept for bookkeeping, detached from the person, and without the free-text note
    detached = [p for p in rows(models.Payment) if p.user_id is None]
    assert len(detached) == 1 and detached[0].amount_twd == 199 and detached[0].note is None
    assert [p.note for p in rows(models.Payment, user_id=b.id)] == ["bank transfer for bao"]

    gone = web.get("/settings")  # the old cookie is still in the jar here, but it opens nothing any more
    assert gone.status_code == 303 and gone.headers["location"].startswith("/login")


def test_delete_flash_is_shown_after_redirect(web, two_learners):
    a, _ = two_learners
    token = login_as(en(web), a)
    r = web.post("/settings/delete", data=delete_form(token))
    assert "hc_flash=" in r.headers["set-cookie"]
    page = web.get("/login")  # the redirect target "/" belongs to another slice; any page shows the flash
    assert i18n.t("en", "account.flash.deleted") in page.text


@pytest.mark.parametrize("override,field", [
    ({"password": "wrong password"}, "password"), ({"password": ""}, "password"),
    ({"confirm": ""}, "confirm"), ({"confirm": "remove"}, "confirm"), ({"confirm": "DELET"}, "confirm"),
])
def test_delete_needs_the_password_and_the_word(web, two_learners, override, field):
    a, _ = two_learners
    token = login_as(en(web), a)
    r = web.post("/settings/delete", data=delete_form(token, **override))
    assert r.status_code == 400 and f'id="e-{field}"' in r.text
    assert count(models.User, id=a.id) == 1 and all(count(m, user_id=a.id) >= 1 for m in LEARNER_TABLES)
    assert not rows(models.AuditLog, action="account_deleted")


def test_delete_needs_csrf_and_a_session(web, two_learners):
    a, _ = two_learners
    login_as(web, a)
    assert web.post("/settings/delete", data={"password": PASSWORD, "confirm": "DELETE"}).status_code == 403
    assert web.post("/settings/delete", data=delete_form("forged")).status_code == 403
    assert count(models.User, id=a.id) == 1
    anonymous = second_client(web)
    anonymous.get("/login")
    r = anonymous.post("/settings/delete", data=delete_form(anonymous.cookies.get("hc_csrf")))
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_a_learner_cannot_delete_somebody_else_by_naming_them(web, two_learners):
    a, b = two_learners
    token = login_as(web, a)
    r = web.post("/settings/delete", data=delete_form(token, user_id=str(b.id), public_id=b.public_id, email="bao@example.com"))
    assert r.status_code == 303
    assert count(models.User, id=a.id) == 0 and count(models.User, id=b.id) == 1
    assert all(count(m, user_id=b.id) >= 1 for m in LEARNER_TABLES)
    # and B's password is not what is checked: A's own password is
    token = login_as(web, b)
    r = web.post("/settings/delete", data=delete_form(token, password="not bao's password"))
    assert r.status_code == 400 and count(models.User, id=b.id) == 1


def _make_admin(email):
    with dbmod.write_session() as db:
        admin = models.User(email=email, password_hash=hash_password(PASSWORD), role="admin", onboarded_at=utcnow(), locale="en")
        db.add(admin)
        db.flush()
        return admin


def test_the_only_admin_cannot_delete_themselves(web):
    admin = _make_admin("boss@example.com")
    token = login_as(en(web), admin)
    r = web.post("/settings/delete", data=delete_form(token))
    assert r.status_code == 400 and i18n.t("en", "account.err.last_admin") in r.text
    assert count(models.User, id=admin.id) == 1 and not rows(models.AuditLog, action="account_deleted")


def test_an_admin_can_delete_themselves_when_another_admin_exists_and_teachers_always_can(web):
    first, second = _make_admin("boss1@example.com"), _make_admin("boss2@example.com")
    token = login_as(web, first)
    assert web.post("/settings/delete", data=delete_form(token)).status_code == 303
    assert count(models.User, id=first.id) == 0 and count(models.User, role="admin") == 1
    # the last one standing is protected again
    token = login_as(web, second)
    assert web.post("/settings/delete", data=delete_form(token)).status_code == 400
    with dbmod.write_session() as db:
        teacher = models.User(email="teach@example.com", password_hash=hash_password(PASSWORD), role="teacher")
        db.add(teacher)
        db.flush()
    token = login_as(web, teacher)
    assert web.post("/settings/delete", data=delete_form(token)).status_code == 303 and count(models.User, id=teacher.id) == 0
    log = rows(models.AuditLog, action="account_deleted", target="(deleted account)")[-1]  # the admin's own deletion came first
    assert log.detail == {"role": "teacher"}


# ----------------------------------------------------------------------------- pages: three languages, no raw keys
def _namespace_keys() -> list[str]:
    return [k for k in i18n.catalog("en") if k.startswith(("auth.", "account."))]


@pytest.mark.parametrize("locale", LOCALES)
def test_account_pages_show_translated_text_never_raw_keys(web, monkeypatch, locale):
    learner = new_learner("fresh@example.com", locale=locale)
    token = login_as(web, learner)
    web.cookies.set("hc_lang", locale)
    pages = [web.get("/onboarding"),
             web.post("/onboarding", data=onboard_form(token, consent_data="0", daily_minutes="1", display_name="x" * 70))]
    monkeypatch.setenv("HUAYU_LLM_PROVIDER", "none")
    ready = ready_user("ready@example.com", locale=locale)
    token = login_as(web, ready)
    base = {"csrf": token, "section": "profile", "locale": "xx", "display_name": "x" * 70, "timezone": "No/Where", "mode": "turbo"}
    pages += [web.get("/settings"), web.post("/settings", data=base),
              web.post("/settings", data=pw_form(token, current="nope", new="short")),
              web.post("/settings", data=pw_form(token, new2="different")),
              web.post("/settings", data=pw_form(token, new=PASSWORD)),
              web.post("/settings/delete", data=delete_form(token, password="nope", confirm="")),
              web.post("/settings", data={**base, "locale": locale, "display_name": "ok", "timezone": "UTC", "mode": "active"})]
    web.post("/settings/consent", data={"csrf": token, "llm_transfer_agreed": "1"})
    pages.append(web.get("/settings"))  # AI consent switched on
    admin = _make_admin("boss@example.com")
    second = second_client(web)
    token = login_as(second, admin)
    second.cookies.set("hc_lang", locale)
    pages.append(second.post("/settings/delete", data=delete_form(token)))  # last admin
    for page in pages:
        assert page.status_code in (200, 400, 303), page.url
        if page.status_code == 303:
            continue
        for key in _namespace_keys() + ["common.flash.saved", "common.flash.error"]:
            assert key not in page.text, (locale, page.url, key)
        for placeholder in ("{min}", "{max_password}", "{max_name}", "{word}", "{n}"):
            assert placeholder not in page.text, (locale, page.url, placeholder)
    settings_page = next(p for p in pages if p.url.path == "/settings" and p.status_code == 200 and 'action="/settings/consent"' in p.text)
    assert {"vi": "Cài đặt", "zh-TW": "設定", "en": "Settings"}[locale] in settings_page.text
    assert {"vi": "Bắt đầu học", "zh-TW": "開始學習", "en": "Start learning"}[locale] in pages[0].text
