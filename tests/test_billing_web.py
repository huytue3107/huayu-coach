"""Slice D: the learner-facing /billing page. It must stay reachable for expired learners and never collect payment data."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES, get_settings
from app.services import billing
from app.timeutil import utcnow
from tests.conftest import login_as, make_user
from tests.test_admin import assert_balanced


@pytest.fixture(autouse=True)
def _only_my_routers(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "admin,billing_web")


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("HUAYU_BILLING_ENABLED", "true")
    monkeypatch.setenv("HUAYU_SUPPORT_EMAIL", "help@huayu.example")
    monkeypatch.setenv("HUAYU_PAYMENT_INSTRUCTIONS", "Bank 812, account 0123 4567\nName: Huayu Coach\n<script>alert(1)</script>")
    get_settings.cache_clear()
    with dbmod.write_session() as s:
        billing.seed_plans(s)


def learner(locale="en", role="learner", **kw):
    with dbmod.write_session() as s:
        user = make_user(s, role=role)
        user.locale = locale
        for key, value in kw.items():
            setattr(user, key, value)
        return user


def open_billing(web, user, **kw):
    web.cookies.clear()
    login_as(web, user)
    return web.get("/billing", **kw)


def main_of(response) -> str:
    return response.text[response.text.index("<main"):response.text.index("</main>")]


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_anonymous_is_sent_to_login_and_the_page_never_changes_state(web):
    r = web.get("/billing")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/billing"
    user = learner()
    login_as(web, user)
    assert web.post("/billing", data={"csrf": "x"}).status_code == 405  # there is nothing to submit: no POST route
    assert web.get("/billing").status_code == 200
    with dbmod.read_session() as s:
        assert s.query(models.Subscription).count() == 0 and s.query(models.Payment).count() == 0


def test_free_pilot_says_so_and_hides_prices(web):
    r = open_billing(web, learner())
    assert r.status_code == 200
    text = plain(main_of(r))
    assert "Free during the pilot" in text and "everything is free" in text
    assert "NT$" not in text and "How to pay" not in text and "Continue learning" in text


def test_trial_shows_end_date_in_the_learners_time_zone_and_the_plans(web, billing_on):
    user = learner(timezone="Asia/Taipei")
    with dbmod.write_session() as s:
        s.add(models.Subscription(user_id=user.id, status="trialing", started_at=utcnow(),
                                  current_period_end=datetime(2099, 12, 1, 17, 0, tzinfo=timezone.utc)))
    text = plain(main_of(open_billing(web, user)))
    assert "Free access" in text and "Everything is open for you until 2099-12-02." in text, "17:00 UTC is already the 2nd in Taipei"
    assert "NT$ 199" in text and "NT$ 799" in text and "Monthly" in text and "30 days of access" in text and "150 days of access" in text
    assert "Continue learning" in text and "has ended" not in text


def test_plan_names_follow_the_language(web, billing_on):
    user = learner(locale="vi")
    assert "Theo tháng" in open_billing(web, user).text
    with dbmod.write_session() as s:
        s.get(models.User, user.id).locale = "zh-TW"
    r = open_billing(web, user)
    assert "月方案" in r.text and "學期方案（5 個月）" in r.text and "NT$ 199" in r.text
    with dbmod.write_session() as s:  # a plan with no name for this language falls back to English, then to its code
        s.add(models.BillingPlan(code="odd", name={"en": "Only English"}, price_twd=5, period_days=3))
        s.add(models.BillingPlan(code="bare", name={}, price_twd=6, period_days=3))
    text = open_billing(web, user).text
    assert "Only English" in text and "bare" in text


def test_expired_learner_can_still_open_billing_and_is_told_what_to_do(web, billing_on):
    user = learner()
    with dbmod.read_session() as s:
        assert not billing.access(s, s.get(models.User, user.id)).allowed
    r = open_billing(web, user)
    assert r.status_code == 200, "billing is where an expired learner lands, so it is never blocked by the access check"
    text = plain(main_of(r))
    assert "Your free access has ended" in text and "Choose a plan below" in text and "Your progress is saved" in text
    assert "Continue learning" not in text and "NT$ 199" in text
    # an old, lapsed subscription: the end date is shown
    with dbmod.write_session() as s:
        s.add(models.Subscription(user_id=user.id, status="trialing", started_at=utcnow() - timedelta(days=20),
                                  current_period_end=datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)))
    assert "Your access ended on 2026-03-09." in plain(main_of(open_billing(web, user)))


def test_active_plan_and_staff_states(web, billing_on):
    user = learner()
    with dbmod.write_session() as s:
        sub = billing.activate(s, s.get(models.User, user.id), "monthly")
        until = sub.current_period_end.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    text = plain(main_of(open_billing(web, user)))
    assert "Your plan is active" in text and f"Your plan runs until {until}." in text
    for role in ("teacher", "admin"):
        text = plain(main_of(open_billing(web, learner(role=role))))
        assert "Staff account" in text and "NT$" not in text and "How to pay" not in text and "Continue learning" not in text


def test_inactive_plans_are_hidden(web, billing_on):
    with dbmod.write_session() as s:
        s.get(models.BillingPlan, "semester").active = False
    text = plain(main_of(open_billing(web, learner())))
    assert "NT$ 199" in text and "NT$ 799" not in text
    with dbmod.write_session() as s:
        s.get(models.BillingPlan, "monthly").active = False
    assert "No plans are open right now" in plain(main_of(open_billing(web, learner())))


def test_payment_instructions_are_plain_escaped_text_with_line_breaks(web, billing_on):
    user = learner()
    r = open_billing(web, user)
    html = main_of(r)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script" not in html
    assert 'class="pre-line">Bank 812, account 0123 4567\nName: Huayu Coach\n&lt;script&gt;' in html, "line breaks survive (CSS white-space: pre-line)"
    text = plain(html)
    assert f"write your learner code {user.public_id} in the transfer note" in text
    assert "We confirm payments by hand" in text and "never asks for your card number" in text
    assert 'href="mailto:help@huayu.example"' in html
    assert user.email not in r.text, "the page never prints the learner's e-mail"


def test_missing_instructions_and_support_email_are_handled(web, billing_on, monkeypatch):
    monkeypatch.setenv("HUAYU_PAYMENT_INSTRUCTIONS", "  ")
    monkeypatch.setenv("HUAYU_SUPPORT_EMAIL", "")
    get_settings.cache_clear()
    html = main_of(open_billing(web, learner()))
    assert "payment details have not been added yet" in html and "mailto:" not in html and "Questions? Write to" not in html


def test_the_page_collects_no_payment_data(web, billing_on):
    for user in (learner(), learner(role="admin")):
        html = main_of(open_billing(web, user))
        assert "<form" not in html and "<input" not in html and "<textarea" not in html and "<button" not in html
        assert "name=" not in html, "no field of any kind"


def test_bad_time_zone_does_not_break_the_page(web, billing_on):
    user = learner(timezone="Mars/Olympus")
    with dbmod.write_session() as s:
        billing.grant_days(s, s.get(models.User, user.id), 5)
    r = open_billing(web, user)
    assert r.status_code == 200 and "Everything is open for you until" in r.text


@pytest.mark.parametrize("loc", LOCALES)
def test_all_states_render_in_every_language_without_raw_keys(web, billing_on, loc):
    keys = [k for k in i18n.catalog("en") if k.startswith("billing.")]
    now = utcnow()
    expired, trial, paid = learner(loc), learner(loc), learner(loc)
    staff = learner(loc, role="teacher")
    with dbmod.write_session() as s:
        s.add(models.Subscription(user_id=trial.id, status="trialing", started_at=now, current_period_end=now + timedelta(days=3)))
        billing.activate(s, s.get(models.User, paid.id), "semester")
    for user in (expired, trial, paid, staff):
        r = open_billing(web, user)
        assert r.status_code == 200 and f'lang="{loc}"' in r.text
        assert_balanced(r.text, "billing")
        assert not [k for k in keys if k in r.text], (loc, [k for k in keys if k in r.text][:5])
        assert "{date}" not in r.text and "{days}" not in r.text and "{code}" not in r.text


@pytest.mark.parametrize("loc", LOCALES)
def test_pilot_page_in_every_language(web, loc):
    r = open_billing(web, learner(loc))
    keys = [k for k in i18n.catalog("en") if k.startswith("billing.")]
    assert r.status_code == 200 and not [k for k in keys if k in r.text] and "{" not in main_of(r)


def test_locale_files_for_billing_are_complete():
    en = {k for k in i18n.catalog("en") if k.startswith("billing.")}
    assert len(en) >= 20
    for loc in LOCALES:
        assert {k for k in i18n.catalog(loc) if k.startswith("billing.")} == en
