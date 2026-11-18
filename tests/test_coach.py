from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import db as dbmod
from app import models
from app.services import coach
from app.services.plans import CoachError, manual_update, snapshot
from app.timeutil import local_today, utcnow
from tests.conftest import add_attempt, make_user


def with_weak_grammar(db, user):
    """9/20 grammar right; the 11 wrong ones are 7x 了 and 4x 在 -> the slide-7 situation (weak topic 了)."""
    for i in range(20):
        ok = i >= 11
        add_attempt(db, user, f"Q{i:03d}", correct=ok, topic=None if ok else ("了" if i < 7 else "在"))


def plan_values(db, user):
    p = db.get(models.StudyPlan, user.plan.id)
    return p.daily_minutes, p.new_grammar, p.exercise_count, p.version


# ----------------------------------------------------------------------------- proposals never touch the plan
def test_creating_a_proposal_changes_nothing(session):
    u = make_user(session)
    with_weak_grammar(session, u)
    before = plan_values(session, u)
    rec = coach.report_interruption(session, u, "too_hard", "今天的內容太難了")
    assert plan_values(session, u) == before == (30, 5, 8, 1)
    assert rec.status == "pending" and rec.base_plan_version == 1
    assert [(i.action_code, i.params) for i in rec.items] == [
        ("reduce_new_grammar", {"value": 2}), ("change_exercise_count", {"value": 5}), ("review_topic", {"topic": "了"})]
    assert rec.interruption.status == "proposed" and rec.interruption.reason_code == "too_hard"
    assert session.scalar(select(models.PlanChange).where(models.PlanChange.result == "accept")) is None


def test_reason_is_validated_and_text_is_cleaned(session):
    u = make_user(session)
    with pytest.raises(CoachError, match="invalid_reason"):
        coach.report_interruption(session, u, "lazy")
    rec = coach.report_interruption(session, u, "need_rest", "  tired\x00\x07 \n" + "x" * 900)
    assert len(rec.interruption.self_report) == 500 and "\x00" not in rec.interruption.self_report


def test_new_report_supersedes_the_old_pending_proposal(session):
    u = make_user(session)
    first = coach.report_interruption(session, u, "too_hard")
    second = coach.report_interruption(session, u, "no_time")
    assert first.status == "superseded" and second.status == "pending"
    assert coach.pending_recommendation(session, u).id == second.id
    with pytest.raises(CoachError, match="already_processed"):
        coach.decide(session, u, first.id, "accept", [first.items[0].id])  # the stale one can't be applied


def test_report_flood_is_limited(session):
    u = make_user(session)
    for _ in range(coach.MAX_REPORTS_PER_DAY):
        coach.report_interruption(session, u, "need_rest")
    with pytest.raises(CoachError, match="too_many_reports"):
        coach.report_interruption(session, u, "need_rest")


# ----------------------------------------------------------------------------- decisions
def test_accept_all_applies_everything_and_writes_the_audit_trail(session):
    u = make_user(session)
    with_weak_grammar(session, u)
    rec = coach.report_interruption(session, u, "too_hard")
    res = coach.decide(session, u, rec.id, "accept", [i.id for i in rec.items])
    assert res.status == "accepted" and res.plan_changed and (res.old_version, res.new_version) == (1, 2)
    assert plan_values(session, u) == (30, 2, 5, 2)
    assert session.get(models.StudyPlan, u.plan.id).review_topics[0]["topic"] == "了"
    assert rec.interruption.status == "accepted"
    assert all(i.status == "accepted" for i in rec.items)
    pc = session.scalars(select(models.PlanChange).where(models.PlanChange.recommendation_id == rec.id)).one()
    assert (pc.result, pc.old_version, pc.new_version) == ("accept", 1, 2)
    assert pc.old_values["new_grammar"] == 5 and pc.new_values["new_grammar"] == 2


def test_partial_acceptance_applies_only_the_chosen_items(session):
    u = make_user(session)
    with_weak_grammar(session, u)
    rec = coach.report_interruption(session, u, "too_hard")
    res = coach.decide(session, u, rec.id, "accept", [rec.items[1].id])  # only the exercise count
    assert res.status == "partial" and plan_values(session, u) == (30, 5, 5, 2)
    assert [i.status for i in rec.items] == ["rejected", "accepted", "rejected"]


def test_reject_leaves_the_plan_untouched_and_is_recorded(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    res = coach.decide(session, u, rec.id, "reject")
    assert res.status == "rejected" and not res.plan_changed and plan_values(session, u) == (30, 5, 8, 1)
    assert rec.interruption.status == "declined" and all(i.status == "rejected" for i in rec.items)
    pc = session.scalars(select(models.PlanChange).where(models.PlanChange.result == "reject")).one()
    assert pc.old_version == pc.new_version == 1


def test_deciding_twice_is_refused_so_a_double_tap_cannot_apply_twice(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    ids = [i.id for i in rec.items]
    coach.decide(session, u, rec.id, "accept", ids)
    values = plan_values(session, u)
    with pytest.raises(CoachError, match="already_processed"):
        coach.decide(session, u, rec.id, "accept", ids)
    with pytest.raises(CoachError, match="already_processed"):
        coach.decide(session, u, rec.id, "reject")
    assert plan_values(session, u) == values


@pytest.mark.parametrize("ids,code", [([], "choose_items"), (None, "choose_items"), ([999999], "item_not_in_recommendation")])
def test_invalid_item_selection(session, ids, code):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    with pytest.raises(CoachError, match=code):
        coach.decide(session, u, rec.id, "accept", ids)


def test_duplicate_item_ids_are_refused(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    with pytest.raises(CoachError, match="duplicate_item"):
        coach.decide(session, u, rec.id, "accept", [rec.items[0].id, rec.items[0].id])


def test_nobody_can_decide_someone_elses_proposal(session):
    owner, other = make_user(session), make_user(session)
    rec = coach.report_interruption(session, owner, "too_hard")
    with pytest.raises(CoachError, match="recommendation_not_found"):
        coach.decide(session, other, rec.id, "accept", [rec.items[0].id])
    assert plan_values(session, owner) == (30, 5, 8, 1)


def test_a_proposal_cannot_be_applied_after_the_plan_moved_on(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    u.plan.version += 1  # e.g. another device changed the plan
    with pytest.raises(CoachError, match="stale_plan"):
        coach.decide(session, u, rec.id, "accept", [i.id for i in rec.items])


def test_manual_plan_edit_supersedes_pending_suggestions(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    manual_update(session, u, daily_minutes=45, new_grammar=6, exercise_count=10, expected_version=1)
    assert rec.status == "superseded" and plan_values(session, u) == (45, 6, 10, 2)
    with pytest.raises(CoachError, match="stale_plan"):
        manual_update(session, u, daily_minutes=20, new_grammar=3, exercise_count=6, expected_version=1)
    with pytest.raises(CoachError, match="invalid_daily_minutes"):
        manual_update(session, u, daily_minutes=500, new_grammar=3, exercise_count=6, expected_version=2)
    with pytest.raises(CoachError, match="no_change"):
        manual_update(session, u, daily_minutes=45, new_grammar=6, exercise_count=10, expected_version=2)


def test_rest_pauses_the_learner_without_touching_the_plan(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "need_rest")
    res = coach.decide(session, u, rec.id, "accept", [rec.items[0].id])
    assert res.status == "accepted" and not res.plan_changed and res.new_version == res.old_version == 1
    assert u.paused_until == local_today(u.timezone) + timedelta(days=1) and coach.is_paused(u)
    coach.resume(session, u)
    assert not coach.is_paused(u)


def test_a_workload_cut_that_no_longer_applies_is_refused_not_silently_skipped(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    item = rec.items[0]
    item.params = {"value": 9}  # tampered row: would RAISE new_grammar from 5 to 9
    with pytest.raises(CoachError, match="invalid_item:must_reduce_new_grammar"):
        coach.decide(session, u, rec.id, "accept", [item.id])
    assert plan_values(session, u)[1] == 5


# ----------------------------------------------------------------------------- atomicity and concurrency
def test_a_failure_in_the_middle_rolls_everything_back(engine, monkeypatch):
    with dbmod.write_session() as s:
        u = make_user(s)
        rec = coach.report_interruption(s, u, "too_hard")
        uid, rid, ids = u.id, rec.id, [i.id for i in rec.items]

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(coach, "PlanChange", boom)  # fails AFTER the plan row was already modified in memory
    with pytest.raises(RuntimeError):
        with dbmod.write_session() as s:
            coach.decide(s, s.get(models.User, uid), rid, "accept", ids)
    with dbmod.read_session() as s:
        plan = s.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == uid))
        rec = s.get(models.Recommendation, rid)
        assert (plan.new_grammar, plan.exercise_count, plan.version) == (5, 8, 1)
        assert rec.status == "pending" and all(i.status == "pending" for i in rec.items)
        assert s.get(models.Interruption, rec.interruption_id).status == "proposed"


def test_two_simultaneous_confirmations_apply_the_change_exactly_once(engine):
    with dbmod.write_session() as s:
        u = make_user(s)
        rec = coach.report_interruption(s, u, "too_hard")
        uid, rid, ids = u.id, rec.id, [i.id for i in rec.items]
    barrier, outcomes = threading.Barrier(2), []

    def attempt():
        barrier.wait()
        try:
            with dbmod.write_session() as s:
                coach.decide(s, s.get(models.User, uid), rid, "accept", ids)
            outcomes.append("ok")
        except CoachError as e:
            outcomes.append(e.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["already_processed", "ok"]
    with dbmod.read_session() as s:
        plan = s.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == uid))
        assert plan.version == 2  # bumped once, not twice
        n = s.scalar(select(func.count()).select_from(models.PlanChange).where(models.PlanChange.result == "accept"))
        assert n == 1  # one audit row, not two


# ----------------------------------------------------------------------------- the loop closes
def test_studying_again_marks_recent_interruptions_resumed(session):
    u = make_user(session)
    old = coach.report_interruption(session, u, "too_hard", now=utcnow() - timedelta(days=10))
    new = coach.report_interruption(session, u, "need_rest")
    coach.decide(session, u, new.id, "reject")
    assert coach.mark_resumed(session, u) == 1
    assert new.interruption.status == "resumed" and new.interruption.resumed_at is not None
    assert old.interruption.status == "proposed"  # older than 7 days: not counted as a comeback


def test_feedback_is_validated_and_only_once_after_deciding(session):
    u = make_user(session)
    rec = coach.report_interruption(session, u, "too_hard")
    with pytest.raises(CoachError, match="decide_first"):
        coach.save_feedback(session, u, rec.id, 4, 4, 2)
    coach.decide(session, u, rec.id, "reject")
    with pytest.raises(CoachError, match="invalid_rating"):
        coach.save_feedback(session, u, rec.id, 6, 4, 2)
    coach.save_feedback(session, u, rec.id, 4, 5, 1, "ok")
    with pytest.raises(CoachError, match="already_rated"):
        coach.save_feedback(session, u, rec.id, 4, 5, 1)


def test_emergency_pause_is_never_blocked(session):
    u = make_user(session)
    until = coach.pause(session, u, None)
    assert until.year == 9999 and coach.is_paused(u)
    coach.pause(session, u, 3)
    assert u.paused_until == local_today(u.timezone) + timedelta(days=3)
    with pytest.raises(CoachError, match="invalid_rest_days"):
        coach.pause(session, u, 99)
