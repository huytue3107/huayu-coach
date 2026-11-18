"""Slice C over HTTP: I'm stuck -> proposal -> decide -> done -> feedback, plus rest and the plan page."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta

import pytest
from markupsafe import escape
from sqlalchemy import func, select

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES, get_settings
from app.domain import actions
from app.services import coach, coach_text, consent, llm
from app.timeutil import local_today, utcnow
from tests.conftest import add_attempt, login_as, make_user, onboarded_user


@pytest.fixture(autouse=True)
def only_the_coach_router(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "coach")


# ----------------------------------------------------------------------------- helpers
def learner(locale: str = "en", attempts: str = "weak", **kw) -> models.User:
    """An onboarded learner. attempts: "weak" = 9/20 grammar right (7 wrong on 了), "few" = 2 answers, "none"."""
    with dbmod.write_session() as s:
        u = onboarded_user(s, **kw)
        u.locale = locale
        if attempts == "weak":
            for i in range(20):
                ok = i >= 11
                add_attempt(s, u, f"Q{i:03d}", correct=ok, topic=None if ok else ("了" if i < 7 else "在"))
        elif attempts == "few":
            add_attempt(s, u, "Q900", correct=True)
            add_attempt(s, u, "Q901", correct=False, topic="了")
    return u


def says(html: str, key: str, locale: str = "en", **kw) -> bool:
    """True if the page contains this i18n string (HTML-escaped the way Jinja does)."""
    return str(escape(i18n.t(locale, key, **kw))) in html


def rec_id_from(resp) -> int:
    assert resp.status_code == 303, resp.text[:300]
    m = re.fullmatch(r"/proposal/(\d+)", resp.headers["location"])
    assert m, resp.headers["location"]
    return int(m.group(1))


def stuck(web, token: str, reason: str, **extra) -> int:
    return rec_id_from(web.post("/stuck", data={"csrf": token, "reason": reason, **extra}))


def item_ids(rec_id: int) -> list[int]:
    with dbmod.read_session() as s:
        return list(s.scalars(select(models.SuggestionItem.id).where(models.SuggestionItem.recommendation_id == rec_id)
                              .order_by(models.SuggestionItem.id)))


def plan_row(uid: int) -> tuple[int, int, int, int]:
    with dbmod.read_session() as s:
        p = s.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == uid))
        return p.daily_minutes, p.new_grammar, p.exercise_count, p.version


def count(model, **where) -> int:
    with dbmod.read_session() as s:
        return s.scalar(select(func.count()).select_from(model).filter_by(**where))


def paused_until(uid: int):
    with dbmod.read_session() as s:
        return s.get(models.User, uid).paused_until


def decide(web, token: str, rec_id: int, decision: str, ids: list[int] | None = None):
    data: dict = {"csrf": token, "decision": decision}
    if ids is not None:
        data["item_id"] = ids
    return web.post(f"/proposal/{rec_id}/decide", data=data)


def flash_on(web, resp, key: str, locale: str = "en", **kw) -> bool:
    """Follow a redirect and check the one-time flash message on the next page."""
    return says(web.get(resp.headers["location"]).text, key, locale, **kw)


# ----------------------------------------------------------------------------- GET /stuck
def test_stuck_page_offers_four_reasons_a_private_note_and_reassurance(web):
    login_as(web, learner())
    r = web.get("/stuck")
    assert r.status_code == 200
    for reason in actions.REASONS:
        assert f'name="reason" value="{reason}"' in r.text
    assert says(r.text, "coach.stuck.lead") and says(r.text, "coach.stuck.promise") and says(r.text, "coach.stuck.note_hint")
    assert 'maxlength="500"' in r.text and 'min="5" max="240"' in r.text
    assert "<script" not in r.text and 'name="csrf"' in r.text


def test_stuck_page_points_to_an_open_suggestion(web):
    u = learner()
    tok = login_as(web, u)
    assert "/proposal/" not in web.get("/stuck").text
    rec_id = stuck(web, tok, "too_hard")
    assert f'href="/proposal/{rec_id}"' in web.get("/stuck").text


# ----------------------------------------------------------------------------- POST /stuck
@pytest.mark.parametrize("reason,title,code", [
    ("too_hard", "Fewer new grammar points", "reduce_new_grammar"), ("no_time", "Shorter daily study time", "change_daily_minutes"),
    ("need_rest", "Take a short rest", "take_rest"), ("distracted", "Short focus blocks", "pomodoro")])
def test_each_of_the_four_reasons_leads_to_a_matching_proposal(web, reason, title, code):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, reason, note="  private words  ")
    page = web.get(f"/proposal/{rec_id}")
    assert page.status_code == 200 and title in page.text
    assert "private words" not in page.text  # the note is never echoed back or sent anywhere
    with dbmod.read_session() as s:
        rec = s.get(models.Recommendation, rec_id)
        assert rec.status == "pending" and rec.items[0].action_code == code
        assert rec.interruption.reason_code == reason and rec.interruption.self_report == "private words"
    assert plan_row(u.id) == (30, 5, 8, 1)  # creating a proposal changes nothing


def test_available_minutes_shape_the_no_time_proposal(web):
    u = learner(daily_minutes=45)
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "no_time", available_minutes="20")
    page = web.get(f"/proposal/{rec_id}").text
    assert says(page, "coach.evidence.available_minutes", value=20)
    assert says(page, "coach.item.change_daily_minutes", old=45, new=20)


@pytest.mark.parametrize("extra,key", [
    ({"reason": "lazy"}, "coach.err.invalid_reason"), ({"reason": ""}, "coach.err.invalid_reason"),
    ({"reason": "no_time", "available_minutes": "abc"}, "coach.err.invalid_available_minutes"),
    ({"reason": "no_time", "available_minutes": "4"}, "coach.err.invalid_available_minutes"),
    ({"reason": "no_time", "available_minutes": "241"}, "coach.err.invalid_available_minutes"),
    ({"reason": "no_time", "available_minutes": "-10"}, "coach.err.invalid_available_minutes"),
    ({"reason": "too_hard", "note": "x" * 501}, "coach.err.note_too_long")])
def test_invalid_stuck_input_is_refused_with_a_friendly_message_and_nothing_is_created(web, extra, key):
    u = learner()
    tok = login_as(web, u)
    r = web.post("/stuck", data={"csrf": tok, **extra})
    assert r.status_code == 303 and r.headers["location"] == "/stuck"
    assert flash_on(web, r, key)
    assert count(models.Interruption, user_id=u.id) == 0


def test_note_length_counts_browser_newlines_once_and_minutes_are_ignored_for_other_reasons(web):
    u = learner()
    tok = login_as(web, u)
    note = "\r\n".join(["a" * 98] * 5)  # 5*98 + 4 newlines = 494 characters once CRLF counts as one
    assert stuck(web, tok, "too_hard", note=note, available_minutes="not a number")
    with dbmod.read_session() as s:
        assert s.scalar(select(models.Interruption.self_report)).count("\n") == 4


def test_report_flood_is_limited(web):
    u = learner()
    tok = login_as(web, u)
    with dbmod.write_session() as s:
        for _ in range(coach.MAX_REPORTS_PER_DAY):
            coach.report_interruption(s, s.get(models.User, u.id), "need_rest")
    r = web.post("/stuck", data={"csrf": tok, "reason": "need_rest"})
    assert r.headers["location"] == "/stuck" and flash_on(web, r, "coach.err.too_many_reports")


def test_a_new_report_supersedes_the_open_one(web):
    tok = login_as(web, learner())
    first = stuck(web, tok, "too_hard")
    second = stuck(web, tok, "no_time")
    old = web.get(f"/proposal/{first}")
    assert says(old.text, "coach.proposal.outcome_superseded") and 'name="decision"' not in old.text
    assert f'href="/proposal/{second}"' in old.text and 'href="/stuck"' in old.text
    assert 'name="decision"' in web.get(f"/proposal/{second}").text


# ----------------------------------------------------------------------------- GET /proposal/{id}
def test_proposal_page_is_a_calm_opt_in_review(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    page = web.get(f"/proposal/{rec_id}").text
    assert says(page, "coach.proposal.notice")
    assert says(page, "coach.proposal.glance", titles="Fewer new grammar points, Fewer exercises, Go over a tricky topic again")
    assert says(page, "coach.summary.too_hard") and "AI" not in page.split("<main", 1)[1].split("</main>")[0].replace("AI service", "")
    assert page.count('type="checkbox"') == 3 and " checked" not in page  # nothing is pre-ticked
    assert says(page, "coach.item.reduce_new_grammar", old=5, new=2) and says(page, "coach.item.change_exercise_count", old=8, new=5)
    assert says(page, "coach.item.review_topic", topic="了") and 'class="zh topic-chip" lang="zh-TW">了<' in page
    assert says(page, "coach.evidence.accuracy", skill="grammar", correct=9, total=20, days=14)
    assert says(page, "coach.evidence.weak_topic", topic="了", wrong=7, days=14)
    for value in ("accept_selected", "accept_all", "reject"):
        assert f'name="decision" value="{value}"' in page
    assert 'href="/plan"' in page and "%" not in page.split("<main", 1)[1].split("</main>")[0]


def test_thin_data_is_stated_honestly_not_as_a_percentage(web):
    tok = login_as(web, learner(attempts="few"))
    page = web.get(f"/proposal/{stuck(web, tok, 'too_hard')}").text
    assert says(page, "coach.evidence.few_data", skill="grammar", total=2) and "accuracy" not in page.lower()


def test_ai_wording_is_never_shown_unless_it_exists(web):
    tok = login_as(web, learner())
    page = web.get(f"/proposal/{stuck(web, tok, 'too_hard')}").text
    assert not says(page, "coach.proposal.ai_label")


def test_unknown_and_foreign_and_forged_proposal_ids_are_404(web):
    owner, other = learner(), learner()
    tok = login_as(web, owner)
    rec_id = stuck(web, tok, "too_hard")
    ids = item_ids(rec_id)
    tok = login_as(web, other)
    for url in (f"/proposal/{rec_id}", f"/proposal/{rec_id}/done", "/proposal/999999", "/proposal/0", "/proposal/abc",
                f"/proposal/{10 ** 30}", f"/proposal/{10 ** 30}/done"):
        assert web.get(url).status_code == 404, url
    for url, data in ((f"/proposal/{rec_id}/decide", {"decision": "accept_all"}),
                      (f"/proposal/{rec_id}/decide", {"decision": "accept_selected", "item_id": ids}),
                      (f"/proposal/{rec_id}/decide", {"decision": "reject"}), (f"/proposal/{rec_id}/feedback", {"clarity": "3"}),
                      ("/proposal/999999/decide", {"decision": "reject"}), (f"/proposal/{10 ** 30}/decide", {"decision": "reject"})):
        assert web.post(url, data={"csrf": tok, **data}).status_code == 404, url
    assert plan_row(owner.id) == (30, 5, 8, 1) and count(models.PlanChange, recommendation_id=rec_id) == 0
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).status == "pending"


def test_stored_text_is_escaped(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    with dbmod.write_session() as s:
        item = s.scalars(select(models.SuggestionItem).where(models.SuggestionItem.action_code == "review_topic")).one()
        item.params = {"topic": "<img src=x onerror=alert(1)>"}
    page = web.get(f"/proposal/{rec_id}").text
    assert "<img src=x" not in page and "&lt;img" in page


# ----------------------------------------------------------------------------- POST decide + GET done
def test_partial_accept_applies_only_the_ticked_items_and_the_done_page_says_exactly_what_changed(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    ids = item_ids(rec_id)
    r = decide(web, tok, rec_id, "accept_selected", [ids[1]])  # only the exercise count
    assert r.status_code == 303 and r.headers["location"] == f"/proposal/{rec_id}/done"
    assert plan_row(u.id) == (30, 5, 5, 2)  # bumped exactly once
    done = web.get(f"/proposal/{rec_id}/done")
    assert done.status_code == 200 and says(done.text, "coach.done.title_changed")
    assert "<li>Exercises per day: 8 → 5</li>" in done.text and "<li>New grammar points per day: 5 → 2</li>" not in done.text
    assert says(done.text, "coach.done.version", version=2) and says(done.text, "coach.done.partial_note")
    assert says(done.text, "coach.item.reduce_new_grammar", old=5, new=2)  # listed as left out, not applied
    with dbmod.read_session() as s:
        pc = s.scalars(select(models.PlanChange).where(models.PlanChange.recommendation_id == rec_id)).one()
        assert (pc.result, pc.old_version, pc.new_version, pc.source) == ("partial", 1, 2, "proposal")
        assert s.get(models.Interruption, s.get(models.Recommendation, rec_id).interruption_id).status == "accepted"
    page = web.get(f"/proposal/{rec_id}")  # decided: the outcome replaces the form
    assert says(page.text, "coach.proposal.outcome_partial") and 'name="decision"' not in page.text
    assert not says(page.text, "coach.proposal.notice")


def test_accept_all_applies_everything(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    assert decide(web, tok, rec_id, "accept_all").headers["location"] == f"/proposal/{rec_id}/done"
    assert plan_row(u.id) == (30, 2, 5, 2)
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).status == "accepted"
        assert [t["topic"] for t in s.scalar(select(models.StudyPlan.review_topics))] == ["了"]
    done = web.get(f"/proposal/{rec_id}/done").text
    assert "<li>New grammar points per day: 5 → 2</li>" in done and "<li>Exercises per day: 8 → 5</li>" in done
    assert "<li>Topics to go over again: not set → 了</li>" in done
    plan = web.get("/plan").text  # the review topic shows up on the plan page, in Chinese markup
    assert 'class="zh" lang="zh-TW">了<' in plan


def test_rejecting_leaves_the_plan_exactly_as_it_was(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    assert decide(web, tok, rec_id, "reject").headers["location"] == f"/proposal/{rec_id}/done"
    assert plan_row(u.id) == (30, 5, 8, 1)
    done = web.get(f"/proposal/{rec_id}/done").text
    assert says(done, "coach.done.title_rejected") and says(done, "coach.done.rejected") and not says(done, "coach.done.title_changed")
    assert "→" not in done.split("<main", 1)[1].split("</main>")[0]


def test_rest_only_proposal_never_says_the_plan_was_updated(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "need_rest")
    decide(web, tok, rec_id, "accept_all")
    assert plan_row(u.id) == (30, 5, 8, 1)
    assert paused_until(u.id) == local_today(u.timezone) + timedelta(days=1)
    done = web.get(f"/proposal/{rec_id}/done").text
    assert says(done, "coach.done.title_rest") and not says(done, "coach.done.title_changed") and "plan version" not in done
    assert says(done, "coach.done.rest_chosen", date=coach_text.format_date("en", local_today(u.timezone) + timedelta(days=1)))
    assert 'href="/rest"' in done


def test_accept_selected_with_nothing_ticked_asks_to_choose_and_changes_nothing(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    r = decide(web, tok, rec_id, "accept_selected", [])
    assert r.status_code == 303 and r.headers["location"] == f"/proposal/{rec_id}"
    page = web.get(r.headers["location"]).text
    assert says(page, "coach.err.choose_items") and 'name="decision"' in page  # still decidable
    assert plan_row(u.id) == (30, 5, 8, 1) and count(models.PlanChange, recommendation_id=rec_id) == 0


@pytest.mark.parametrize("data,key", [
    ({"decision": "maybe"}, "coach.err.invalid_decision"), ({}, "coach.err.invalid_decision"),
    ({"decision": "accept_selected", "item_id": ["abc"]}, "coach.err.item_not_in_recommendation"),
    ({"decision": "accept_selected", "item_id": ["999999"]}, "coach.err.item_not_in_recommendation"),
    ({"decision": "accept_selected", "item_id": ["-1"]}, "coach.err.item_not_in_recommendation"),
    ({"decision": "accept_selected", "item_id": [str(i) for i in range(30)]}, "coach.err.item_not_in_recommendation")])
def test_forged_decisions_are_refused_without_a_500(web, data, key):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    r = web.post(f"/proposal/{rec_id}/decide", data={"csrf": tok, **data})
    assert r.status_code == 303 and r.headers["location"] == f"/proposal/{rec_id}" and flash_on(web, r, key)
    assert plan_row(u.id) == (30, 5, 8, 1)


def test_items_of_another_proposal_cannot_be_smuggled_in(web):
    u = learner()
    tok = login_as(web, u)
    first = stuck(web, tok, "too_hard")
    first_ids = item_ids(first)
    second = stuck(web, tok, "too_hard")
    r = decide(web, tok, second, "accept_selected", [first_ids[0]])
    assert flash_on(web, r, "coach.err.item_not_in_recommendation") and plan_row(u.id) == (30, 5, 8, 1)


def test_double_submit_is_harmless(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    first = decide(web, tok, rec_id, "accept_all")
    second = decide(web, tok, rec_id, "accept_all")
    third = decide(web, tok, rec_id, "reject")
    assert {first.headers["location"], second.headers["location"], third.headers["location"]} == {f"/proposal/{rec_id}/done"}
    assert plan_row(u.id) == (30, 2, 5, 2)  # applied once, not twice
    assert count(models.PlanChange, recommendation_id=rec_id) == 1
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).status == "accepted"


def test_a_proposal_made_for_an_older_plan_is_explained_not_applied(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    with dbmod.write_session() as s:  # the plan moved on behind its back (e.g. another device)
        s.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == u.id)).version = 5
    page = web.get(f"/proposal/{rec_id}").text
    assert says(page, "coach.proposal.stale_title") and 'name="decision"' not in page and 'href="/stuck"' in page
    r = decide(web, tok, rec_id, "accept_all")
    assert r.headers["location"] == f"/proposal/{rec_id}" and flash_on(web, r, "coach.err.stale_plan")
    assert plan_row(u.id) == (30, 5, 8, 5)  # nothing applied


def test_deciding_on_a_replaced_proposal_goes_to_a_done_page_that_offers_a_fresh_start(web):
    tok = login_as(web, learner())
    first = stuck(web, tok, "too_hard")
    second = stuck(web, tok, "need_rest")
    r = decide(web, tok, first, "accept_all")
    assert r.headers["location"] == f"/proposal/{first}/done"
    done = web.get(r.headers["location"]).text
    assert says(done, "coach.done.title_superseded") and 'href="/stuck"' in done and f'href="/proposal/{second}"' in done
    assert "/feedback" not in done  # nothing was decided, so nothing to rate


def test_done_redirects_back_to_the_proposal_while_it_is_still_open(web):
    tok = login_as(web, learner())
    rec_id = stuck(web, tok, "too_hard")
    r = web.get(f"/proposal/{rec_id}/done")
    assert r.status_code == 303 and r.headers["location"] == f"/proposal/{rec_id}"


# ----------------------------------------------------------------------------- feedback
def test_feedback_is_collected_once_and_then_replaced_by_thanks(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    decide(web, tok, rec_id, "reject")
    form = web.get(f"/proposal/{rec_id}/done").text
    for name in ("clarity", "helpfulness", "pressure", "comment"):
        assert f'name="{name}"' in form
    assert form.count('type="radio"') == 15
    r = web.post(f"/proposal/{rec_id}/feedback", data={"csrf": tok, "clarity": "4", "helpfulness": "5", "pressure": "1", "comment": "  kind \x00words "})
    assert r.status_code == 303 and r.headers["location"] == f"/proposal/{rec_id}/done"
    after = web.get(r.headers["location"]).text
    assert says(after, "coach.feedback.thanks") and 'name="clarity"' not in after
    with dbmod.read_session() as s:
        fb = s.scalars(select(models.Feedback)).one()
        assert (fb.user_id, fb.clarity, fb.helpfulness, fb.pressure, fb.comment) == (u.id, 4, 5, 1, "kind words")
    again = web.post(f"/proposal/{rec_id}/feedback", data={"csrf": tok, "clarity": "1", "helpfulness": "1", "pressure": "5"})
    assert flash_on(web, again, "coach.err.already_rated") and count(models.Feedback) == 1


@pytest.mark.parametrize("data,key", [
    ({"clarity": "6", "helpfulness": "3", "pressure": "3"}, "coach.err.invalid_rating"),
    ({"clarity": "0", "helpfulness": "3", "pressure": "3"}, "coach.err.invalid_rating"),
    ({"clarity": "x", "helpfulness": "3", "pressure": "3"}, "coach.err.invalid_rating"),
    ({"clarity": "3", "helpfulness": "3"}, "coach.err.invalid_rating"),
    ({"clarity": "3", "helpfulness": "3", "pressure": "3", "comment": "x" * 501}, "coach.err.comment_too_long")])
def test_invalid_feedback_is_refused(web, data, key):
    tok = login_as(web, learner())
    rec_id = stuck(web, tok, "too_hard")
    decide(web, tok, rec_id, "reject")
    r = web.post(f"/proposal/{rec_id}/feedback", data={"csrf": tok, **data})
    assert r.status_code == 303 and flash_on(web, r, key) and count(models.Feedback) == 0


def test_feedback_before_deciding_or_on_a_replaced_proposal_is_refused(web):
    tok = login_as(web, learner())
    first = stuck(web, tok, "too_hard")
    good = {"csrf": tok, "clarity": "3", "helpfulness": "3", "pressure": "3"}
    assert flash_on(web, web.post(f"/proposal/{first}/feedback", data=good), "coach.err.decide_first")
    stuck(web, tok, "no_time")  # replaces `first`
    assert flash_on(web, web.post(f"/proposal/{first}/feedback", data=good), "coach.err.decide_first")
    assert count(models.Feedback) == 0


# ----------------------------------------------------------------------------- rest and resume
def test_rest_page_offers_the_four_options_and_no_resume_when_not_resting(web):
    login_as(web, learner())
    page = web.get("/rest").text
    for value in ("1", "3", "7", "until_back"):
        assert f'name="days" value="{value}"' in page
    assert 'action="/resume"' not in page and says(page, "coach.rest.lead")


@pytest.mark.parametrize("days,delta", [("1", 1), ("3", 3), ("7", 7)])
def test_resting_for_a_few_days_and_coming_back(web, days, delta):
    u = learner()
    tok = login_as(web, u)
    r = web.post("/rest", data={"csrf": tok, "days": days})
    assert r.status_code == 303 and r.headers["location"] == "/rest"
    until = local_today(u.timezone) + timedelta(days=delta)
    assert paused_until(u.id) == until
    page = web.get("/rest").text  # the flash and the status are both on the page
    assert says(page, "coach.flash.rest_started", date=coach_text.format_date("en", until))
    assert says(page, "coach.rest.status_days", date=coach_text.format_date("en", until)) and 'action="/resume"' in page
    back = web.post("/resume", data={"csrf": tok})
    assert back.status_code == 303 and back.headers["location"] == "/today" and paused_until(u.id) is None
    assert plan_row(u.id) == (30, 5, 8, 1)  # resting never touches the plan


def test_resting_until_i_come_back(web):
    u = learner()
    tok = login_as(web, u)
    web.post("/rest", data={"csrf": tok, "days": "until_back"})
    assert paused_until(u.id) == actions.FAR_FUTURE
    page = web.get("/rest").text
    assert says(page, "coach.rest.status_open") and "9999" not in page and 'action="/resume"' in page
    web.post("/resume", data={"csrf": tok})
    assert paused_until(u.id) is None
    assert web.post("/resume", data={"csrf": tok}).status_code == 303  # resuming when not resting is harmless


@pytest.mark.parametrize("days", ["", "0", "2", "30", "-1", "abc", "UNTIL_BACK"])
def test_invalid_rest_lengths_are_refused(web, days):
    u = learner()
    tok = login_as(web, u)
    r = web.post("/rest", data={"csrf": tok, "days": days})
    assert r.headers["location"] == "/rest" and flash_on(web, r, "coach.err.invalid_rest_days") and paused_until(u.id) is None


def test_get_never_changes_state(web):
    u = learner()
    login_as(web, u)
    web.get("/rest?days=3")
    web.get("/stuck?reason=need_rest")
    assert paused_until(u.id) is None and count(models.Interruption) == 0
    assert web.get("/resume").status_code in (404, 405) and web.get("/proposal/1/decide").status_code in (404, 405)  # POST-only


# ----------------------------------------------------------------------------- GET/POST /plan
def test_plan_page_shows_numbers_version_ownership_and_history(web):
    u = learner()
    tok = login_as(web, u)
    page = web.get("/plan").text
    assert says(page, "coach.plan.own") and says(page, "coach.plan.version", version=1)
    assert 'name="expected_version" value="1"' in page and 'value="30"' in page and 'value="5"' in page and 'value="8"' in page
    assert says(page, "coach.history.default_text", minutes=30, grammar=5, exercises=8) and says(page, "coach.plan.review_none")
    rec_id = stuck(web, tok, "too_hard")
    assert f'href="/proposal/{rec_id}"' in web.get("/plan").text  # a pending suggestion is linked
    decide(web, tok, rec_id, "accept_selected", [item_ids(rec_id)[0]])
    page = web.get("/plan").text
    assert says(page, "coach.history.who_partial") and "New grammar points per day: 5 → 2" in page
    assert f'href="/proposal/{rec_id}/done"' in page and f'href="/proposal/{rec_id}"' not in page
    assert says(page, "coach.plan.version", version=2)


def test_manual_edit_can_raise_or_lower_anything_and_is_recorded(web):
    u = learner()
    tok = login_as(web, u)
    r = web.post("/plan", data={"csrf": tok, "daily_minutes": "45", "new_grammar": "6", "exercise_count": "10", "expected_version": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/plan"
    assert plan_row(u.id) == (45, 6, 10, 2)
    page = web.get("/plan").text
    assert says(page, "coach.flash.plan_saved", version=2) and says(page, "coach.history.who_learner")
    assert "Daily study time: 30 → 45 minutes" in page and "Exercises per day: 8 → 10" in page
    assert "Starting plan" in page or says(page, "coach.history.who_default")
    r = web.post("/plan", data={"csrf": tok, "daily_minutes": "20", "new_grammar": "3", "exercise_count": "5", "expected_version": "2"})
    assert plan_row(u.id) == (20, 3, 5, 3)


def test_manual_edit_closes_an_open_suggestion(web):
    u = learner()
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    web.post("/plan", data={"csrf": tok, "daily_minutes": "45", "new_grammar": "6", "exercise_count": "10", "expected_version": "1"})
    assert says(web.get(f"/proposal/{rec_id}").text, "coach.proposal.outcome_superseded")


@pytest.mark.parametrize("data,key", [
    ({"daily_minutes": "500"}, "coach.err.invalid_daily_minutes"), ({"daily_minutes": "9"}, "coach.err.invalid_daily_minutes"),
    ({"daily_minutes": "abc"}, "coach.err.invalid_daily_minutes"), ({"daily_minutes": "-30"}, "coach.err.invalid_daily_minutes"),
    ({"daily_minutes": ""}, "coach.err.invalid_daily_minutes"), ({"daily_minutes": "30.5"}, "coach.err.invalid_daily_minutes"),
    ({"new_grammar": "0"}, "coach.err.invalid_new_grammar"), ({"new_grammar": "11"}, "coach.err.invalid_new_grammar"),
    ({"exercise_count": "2"}, "coach.err.invalid_exercise_count"), ({"exercise_count": "21"}, "coach.err.invalid_exercise_count"),
    ({}, "coach.err.no_change"), ({"expected_version": "7"}, "coach.err.stale_plan"),
    ({"expected_version": "x"}, "coach.err.invalid_form"), ({"expected_version": ""}, "coach.err.invalid_form")])
def test_bad_plan_edits_are_refused_and_change_nothing(web, data, key):
    u = learner()
    tok = login_as(web, u)
    form = {"csrf": tok, "daily_minutes": "30", "new_grammar": "5", "exercise_count": "8", "expected_version": "1", **data}
    r = web.post("/plan", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/plan" and flash_on(web, r, key)
    assert plan_row(u.id) == (30, 5, 8, 1) and count(models.PlanChange, user_id=u.id) == 1


def test_editing_from_a_stale_form_does_not_overwrite_newer_changes(web):
    u = learner()
    tok = login_as(web, u)
    form = {"csrf": tok, "daily_minutes": "45", "new_grammar": "6", "exercise_count": "10", "expected_version": "1"}
    assert web.post("/plan", data=form).status_code == 303  # first tab saves
    stale = web.post("/plan", data={**form, "daily_minutes": "15"})  # second tab still holds version 1
    assert flash_on(web, stale, "coach.err.stale_plan") and plan_row(u.id) == (45, 6, 10, 2)


def test_each_learner_sees_only_their_own_plan_and_history(web):
    a, b = learner(daily_minutes=30), learner(daily_minutes=15)
    tok = login_as(web, a)
    web.post("/plan", data={"csrf": tok, "daily_minutes": "45", "new_grammar": "6", "exercise_count": "10", "expected_version": "1"})
    login_as(web, b)
    page = web.get("/plan").text
    assert 'name="expected_version" value="1"' in page and "30 → 45" not in page and 'value="15"' in page


# ----------------------------------------------------------------------------- gating
def test_anonymous_visitors_are_sent_to_log_in(web):
    for url in ("/stuck", "/plan", "/proposal/1", "/proposal/1/done", "/rest"):
        r = web.get(url)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), url
    from app.security import new_csrf_token
    token = new_csrf_token()
    web.cookies.set("hc_csrf", token)  # a browser whose login expired still holds a valid CSRF cookie
    for url in ("/stuck", "/proposal/1/decide", "/proposal/1/feedback", "/rest", "/resume", "/plan"):
        r = web.post(url, data={"csrf": token})
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), url
        assert web.post(url, data={}).status_code == 403, url  # and without the token nothing is even looked at


def test_learners_who_have_not_finished_onboarding_are_sent_there_except_for_rest(web):
    with dbmod.write_session() as s:
        u = make_user(s)  # no onboarded_at
    tok = login_as(web, u)
    for url in ("/stuck", "/plan", "/proposal/1", "/proposal/1/done"):
        r = web.get(url)
        assert r.status_code == 303 and r.headers["location"] == "/onboarding", url
    for url in ("/stuck", "/plan"):
        assert web.post(url, data={"csrf": tok, "reason": "need_rest"}).headers["location"] == "/onboarding", url
    assert web.get("/rest").status_code == 200  # rest must always work
    assert web.post("/rest", data={"csrf": tok, "days": "1"}).status_code == 303 and paused_until(u.id) is not None
    assert web.post("/resume", data={"csrf": tok}).status_code == 303 and paused_until(u.id) is None


def test_an_expired_subscription_blocks_coaching_but_never_rest(web, monkeypatch):
    monkeypatch.setenv("HUAYU_BILLING_ENABLED", "true")
    get_settings.cache_clear()
    with dbmod.write_session() as s:
        u = onboarded_user(s)
        s.add(models.Subscription(user_id=u.id, status="trialing", provider="trial", started_at=utcnow() - timedelta(days=30),
                                  current_period_end=utcnow() - timedelta(days=16)))
    tok = login_as(web, u)
    for url in ("/stuck", "/plan"):
        assert web.get(url).headers["location"] == "/billing", url
    assert web.post("/stuck", data={"csrf": tok, "reason": "need_rest"}).headers["location"] == "/billing"
    assert count(models.Interruption) == 0
    assert web.get("/rest").status_code == 200
    r = web.post("/rest", data={"csrf": tok, "days": "7"})
    assert r.status_code == 303 and r.headers["location"] == "/rest" and paused_until(u.id) == local_today(u.timezone) + timedelta(days=7)
    assert 'action="/resume"' in web.get("/rest").text
    assert web.post("/resume", data={"csrf": tok}).status_code == 303 and paused_until(u.id) is None


# ----------------------------------------------------------------------------- CSRF
@pytest.mark.parametrize("path,data", [
    ("/stuck", {"reason": "too_hard"}), ("/proposal/{rec}/decide", {"decision": "accept_all"}),
    ("/proposal/{rec}/feedback", {"clarity": "3", "helpfulness": "3", "pressure": "3"}), ("/rest", {"days": "3"}), ("/resume", {}),
    ("/plan", {"daily_minutes": "45", "new_grammar": "6", "exercise_count": "10", "expected_version": "1"})])
@pytest.mark.parametrize("how", ["missing", "wrong", "other_session"])
def test_every_post_needs_a_valid_csrf_token(web, path, data, how):
    from app.security import new_csrf_token
    u = learner()
    with dbmod.write_session() as s:
        rec = coach.report_interruption(s, s.get(models.User, u.id), "too_hard")
        rec_id = rec.id
    tok = login_as(web, u)
    if how == "missing":
        form = dict(data)
    elif how == "wrong":
        form = {"csrf": tok + "x", **data}
    else:
        form = {"csrf": new_csrf_token(), **data}  # validly signed, but not the one in this browser's cookie
    r = web.post(path.format(rec=rec_id), data=form)
    assert r.status_code == 403, (path, how)
    assert plan_row(u.id) == (30, 5, 8, 1) and paused_until(u.id) is None
    assert count(models.Interruption, user_id=u.id) == 1 and count(models.Feedback) == 0
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).status == "pending"


# ----------------------------------------------------------------------------- the AI wording
@pytest.fixture
def llm_configured(monkeypatch):
    monkeypatch.setenv("HUAYU_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUAYU_LLM_API_KEY", "test-key")
    monkeypatch.setenv("HUAYU_LLM_MODEL", "test-model")
    get_settings.cache_clear()


def agree_to_ai(uid: int, agreed: bool = True) -> None:
    with dbmod.write_session() as s:
        consent.record(s, s.get(models.User, uid), agreed)


def test_ai_is_not_called_without_consent_or_without_a_provider(web, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "explain", lambda *a, **k: calls.append(a) or "never")
    u = learner()
    tok = login_as(web, u)
    stuck(web, tok, "too_hard")  # no provider configured, no consent
    agree_to_ai(u.id)
    stuck(web, tok, "too_hard")  # consent but still no provider (llm_provider == "none")
    assert calls == []


def test_ai_is_not_called_when_the_learner_did_not_agree(web, monkeypatch, llm_configured):
    calls = []
    monkeypatch.setattr(llm, "explain", lambda *a, **k: calls.append(a) or "never")
    u = learner()
    tok = login_as(web, u)
    stuck(web, tok, "too_hard")
    agree_to_ai(u.id, False)  # an explicit withdrawal is the latest consent row
    stuck(web, tok, "too_hard")
    assert calls == []


def test_ai_wording_is_requested_after_the_commit_without_the_free_text_and_shown_with_a_label(web, monkeypatch, llm_configured, engine):
    u = learner(locale="vi")
    agree_to_ai(u.id)
    seen: dict = {}

    def fake_explain(reason, items, plan, locale, *a, **k):
        seen["args"] = (reason, items, plan, locale)
        with dbmod.read_session() as s:  # the proposal is already committed and visible to other connections
            seen["committed"] = s.scalar(select(func.count()).select_from(models.Recommendation))
        raw = sqlite3.connect(engine.url.database, timeout=0.2, isolation_level=None)
        try:  # the database write lock must be FREE while the (slow) AI call runs
            raw.execute("BEGIN IMMEDIATE")
            raw.execute("ROLLBACK")
            seen["lock_free"] = True
        except sqlite3.OperationalError:
            seen["lock_free"] = False
        finally:
            raw.close()
        return "Mình giảm ngữ pháp mới từ 5 xuống 2 nhé. Chưa có gì thay đổi cho đến khi bạn xác nhận."

    monkeypatch.setattr(llm, "explain", fake_explain)
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard", note="my very private note")
    assert seen["committed"] == 1 and seen["lock_free"] is True
    reason, items, plan, locale = seen["args"]
    assert reason == "too_hard" and locale == "vi" and plan["daily_minutes"] == 30
    assert [i["action"] for i in items] == ["reduce_new_grammar", "change_exercise_count", "review_topic"]
    assert all(set(i) == {"action", "params", "evidence"} for i in items)
    assert "private" not in json.dumps(seen["args"], ensure_ascii=False)  # never the free text
    with dbmod.read_session() as s:
        rec = s.get(models.Recommendation, rec_id)
        assert rec.explanation_source == "llm" and rec.explanation.startswith("Mình giảm")
    page = web.get(f"/proposal/{rec_id}").text
    assert "Mình giảm ngữ pháp mới từ 5 xuống 2" in page and says(page, "coach.proposal.ai_label", "vi") and says(page, "coach.proposal.ai_note", "vi")
    assert not says(page, "coach.summary.too_hard", "vi") and "Tóm lại:" not in page  # AI text replaces the template summary
    assert plan_row(u.id) == (30, 5, 8, 1)


@pytest.mark.parametrize("outcome", ["raise", "none", "blank", "not_text"])
def test_any_ai_problem_is_silent_and_the_template_wording_is_used(web, monkeypatch, llm_configured, outcome):
    def fake_explain(*a, **k):
        if outcome == "raise":
            raise RuntimeError("provider down")
        return {"none": None, "blank": "   ", "not_text": 42}[outcome]

    monkeypatch.setattr(llm, "explain", fake_explain)
    u = learner()
    agree_to_ai(u.id)
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")  # still redirects to the proposal
    page = web.get(f"/proposal/{rec_id}").text
    assert says(page, "coach.summary.too_hard") and not says(page, "coach.proposal.ai_label")
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).explanation is None


def test_ai_text_is_escaped_and_capped(web, monkeypatch, llm_configured):
    monkeypatch.setattr(llm, "explain", lambda *a, **k: "<script>alert(1)</script>" + "x" * 5000)
    u = learner()
    agree_to_ai(u.id)
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    with dbmod.read_session() as s:
        assert len(s.get(models.Recommendation, rec_id).explanation) == 900
    page = web.get(f"/proposal/{rec_id}").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_ai_text_is_dropped_if_the_proposal_was_replaced_meanwhile(web, monkeypatch, llm_configured):
    u = learner()
    agree_to_ai(u.id)

    def fake_explain(*a, **k):  # while the AI thinks, the learner starts another report
        with dbmod.write_session() as s:
            coach.report_interruption(s, s.get(models.User, u.id), "need_rest")
        return "late words"

    monkeypatch.setattr(llm, "explain", fake_explain)
    tok = login_as(web, u)
    rec_id = stuck(web, tok, "too_hard")
    with dbmod.read_session() as s:
        assert s.get(models.Recommendation, rec_id).status == "superseded" and s.get(models.Recommendation, rec_id).explanation is None


# ----------------------------------------------------------------------------- every page, every language, no raw keys
COACH_KEYS = sorted(k for k in i18n.catalog("en") if k.startswith("coach."))


def assert_clean(html: str, locale: str, where: str) -> None:
    assert html.count("<html") == 1 and f'lang="{locale}"' in html, where
    raw = [k for k in COACH_KEYS if k in html]
    assert not raw, (where, raw[:5])
    assert not re.search(r"\{[a-z_]+\}", html), (where, re.findall(r"\{[a-z_]+\}", html)[:3])
    assert "<script" not in html and "None" not in html, where


@pytest.mark.parametrize("locale", LOCALES)
def test_no_raw_translation_key_or_placeholder_in_any_page_in_any_language(web, locale):
    u = learner(locale=locale)
    tok = login_as(web, u)
    pages: dict[str, str] = {}

    def get(name: str, url: str) -> None:
        r = web.get(url)
        assert r.status_code == 200, (name, r.status_code)
        pages[name] = r.text

    get("stuck", "/stuck")
    first = stuck(web, tok, "too_hard")
    get("proposal_too_hard", f"/proposal/{first}")
    decide(web, tok, first, "accept_selected", [item_ids(first)[0], item_ids(first)[2]])
    get("done_partial", f"/proposal/{first}/done")
    get("proposal_decided", f"/proposal/{first}")
    web.post(f"/proposal/{first}/feedback", data={"csrf": tok, "clarity": "5", "helpfulness": "4", "pressure": "1"})
    get("done_rated", f"/proposal/{first}/done")

    second = stuck(web, tok, "no_time", available_minutes="15")
    get("proposal_no_time", f"/proposal/{second}")
    decide(web, tok, second, "accept_all")
    get("done_changed", f"/proposal/{second}/done")

    third = stuck(web, tok, "need_rest")
    get("proposal_rest", f"/proposal/{third}")
    decide(web, tok, third, "accept_all")
    get("done_rest", f"/proposal/{third}/done")
    get("rest_paused", "/rest")
    web.post("/rest", data={"csrf": tok, "days": "until_back"})
    get("rest_open_ended", "/rest")
    web.post("/resume", data={"csrf": tok})
    get("rest_idle", "/rest")

    fourth = stuck(web, tok, "distracted")
    get("proposal_distracted", f"/proposal/{fourth}")
    decide(web, tok, fourth, "reject")
    get("done_rejected", f"/proposal/{fourth}/done")

    old, new = stuck(web, tok, "too_hard"), stuck(web, tok, "need_rest")
    get("proposal_superseded", f"/proposal/{old}")
    get("done_superseded", f"/proposal/{old}/done")
    with dbmod.write_session() as s:
        s.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == u.id)).version += 3
    get("proposal_stale", f"/proposal/{new}")
    web.post("/stuck", data={"csrf": tok, "reason": "bad"})
    get("stuck_with_error_flash", "/stuck")
    web.post("/plan", data={"csrf": tok, "daily_minutes": "5", "new_grammar": "5", "exercise_count": "8", "expected_version": "1"})
    get("plan_with_error_flash", "/plan")

    few = learner(locale=locale, attempts="few")
    tok2 = login_as(web, few)
    get("proposal_few_data", f"/proposal/{stuck(web, tok2, 'too_hard')}")
    get("plan_fresh", "/plan")

    for name, html in pages.items():
        assert_clean(html, locale, name)
    assert i18n.t(locale, "coach.stuck.title") in pages["stuck"].replace("&#39;", "'")


@pytest.mark.parametrize("locale", LOCALES)
def test_pages_are_written_in_the_learners_language(web, locale):
    tok = login_as(web, learner(locale=locale))
    expected = {"vi": "Chưa có gì thay đổi", "zh-TW": "目前還沒有任何改變", "en": "Nothing has changed yet"}[locale]
    assert expected in web.get(f"/proposal/{stuck(web, tok, 'too_hard')}").text


def test_every_literal_key_used_by_the_templates_exists_in_all_three_languages():
    from pathlib import Path
    folder = Path(__file__).resolve().parent.parent / "app" / "templates" / "coach"
    literal = dynamic = 0
    for path in sorted(folder.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        assert "<script" not in source and "|safe" not in source, path.name
        for key in re.findall(r"""\bt\('([\w.]+)'\s*[,)]""", source):
            literal += 1
            for loc in LOCALES:
                assert key in i18n.catalog(loc), (path.name, key, loc)
        for prefix in re.findall(r"""\bt\('([\w.]+\.)'\s*~""", source):
            dynamic += 1
            assert any(k.startswith(prefix) for k in i18n.catalog("en")), (path.name, prefix)
    assert literal > 50 and dynamic >= 5
