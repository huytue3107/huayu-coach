from __future__ import annotations

from datetime import date

import pytest

from app.domain import actions, diagnosis, levels
from app.domain.actions import ActionError, ItemSpec, PlanState
from app.domain.diagnosis import LearnerStats
from app.domain.scheduler import History, QInfo, pick_questions

TODAY = date(2026, 11, 18)


def stats(**kw) -> LearnerStats:
    return LearnerStats(**kw)


# ----------------------------------------------------------------------------- safety rules
@pytest.mark.parametrize("code,params,plan", [
    ("reduce_new_grammar", {"value": 10}, PlanState(new_grammar=3)),       # the old backend accepted this: it RAISES load
    ("reduce_new_grammar", {"value": 3}, PlanState(new_grammar=3)),        # no-op
    ("reduce_new_grammar", {"value": 0}, PlanState(new_grammar=3)),        # below floor
    ("change_exercise_count", {"value": 20}, PlanState(exercise_count=4)),
    ("change_exercise_count", {"value": 2}, PlanState(exercise_count=8)),
    ("change_daily_minutes", {"value": 90}, PlanState(daily_minutes=30)),
    ("change_daily_minutes", {"value": 5}, PlanState(daily_minutes=30)),
    ("split_session", {"sessions": 1}, PlanState()),
    ("split_session", {"sessions": 9}, PlanState()),
    ("pomodoro", {"focus_minutes": 40}, PlanState()),
    ("take_rest", {"days": 0}, PlanState()),
    ("take_rest", {"days": 8}, PlanState()),
    ("review_topic", {"topic": ""}, PlanState()),
    ("reduce_new_grammar", {"value": None}, PlanState()),                  # the old validInt_ turned null into 0
    ("reduce_new_grammar", {"value": True}, PlanState(new_grammar=3)),     # bool is not an int here
    ("reduce_new_grammar", {"value": "2"}, PlanState(new_grammar=3)),
    ("constructor", {"value": 1}, PlanState()),                            # prototype-key trick from the audit
    ("__proto__", {"value": 1}, PlanState()),
    ("set_mode", {"value": "active"}, PlanState()),                        # nothing can change the learner's mode
])
def test_unsafe_items_are_rejected(code, params, plan):
    with pytest.raises(ActionError):
        actions.validate(code, params, plan)


def test_apply_composes_and_reports_old_new_values():
    plan = PlanState(30, 5, 8)
    e = actions.apply("reduce_new_grammar", {"value": 2}, plan, TODAY)
    e = actions.apply("change_exercise_count", {"value": 5}, e.plan, TODAY, e)
    assert e.plan.new_grammar == 2 and e.plan.exercise_count == 5
    assert e.changed == {"new_grammar": (5, 2), "exercise_count": (8, 5)}
    assert e.plan_changed


def test_take_rest_changes_no_plan_numbers():
    e = actions.apply("take_rest", {"days": 2}, PlanState(), TODAY)
    assert e.rest_until == date(2026, 11, 20) and not e.plan_changed and e.plan == PlanState()


def test_review_topic_expires_in_seven_days_and_keeps_three():
    e = actions.Effect(plan=PlanState())
    for t in ("了", "把", "在", "是"):
        e = actions.apply("review_topic", {"topic": t}, e.plan, TODAY, e)
    assert [t for t, _ in e.plan.review_topics] == ["把", "在", "是"]
    assert e.plan.review_topics[-1][1] == "2026-11-25"


# ----------------------------------------------------------------------------- engine
def test_slide_7_example_is_reproducible():
    """Deck slide 7: 5 new patterns / 8 exercises -> 2 / 5 plus review of yesterday's topic. The old flow gave only 5 -> 3."""
    items = diagnosis.build_items("too_hard", PlanState(30, 5, 8),
                                  stats(grammar_correct=9, grammar_total=20, weak_topics=[("了", 4)]), TODAY)
    assert [(i.action_code, i.params) for i in items] == [
        ("reduce_new_grammar", {"value": 2}), ("change_exercise_count", {"value": 5}), ("review_topic", {"topic": "了"})]
    ev = items[0].evidence
    assert {"type": "accuracy", "skill": "grammar", "correct": 9, "total": 20, "days": 14} in ev


def test_percent_is_quoted_only_with_enough_attempts():
    items = diagnosis.build_items("too_hard", PlanState(30, 5, 8), stats(grammar_correct=1, grammar_total=2), TODAY)
    kinds = {e["type"] for e in items[0].evidence}
    assert "accuracy" not in kinds and "few_data" in kinds  # the old demo quoted "45%" from 2 answers


def test_one_wrong_answer_is_not_a_weak_topic():
    items = diagnosis.build_items("too_hard", PlanState(30, 5, 8), stats(weak_topics=[("了", 1)]), TODAY)
    assert "review_topic" not in [i.action_code for i in items]


@pytest.mark.parametrize("reason", actions.REASONS)
@pytest.mark.parametrize("plan", [PlanState(30, 5, 8), PlanState(15, 2, 5), PlanState(10, 1, 3), PlanState(90, 10, 20)])
def test_engine_always_returns_valid_nonempty_capped_items(reason, plan):
    items = diagnosis.build_items(reason, plan, stats(weak_topics=[("了", 3)]), TODAY)
    assert 1 <= len(items) <= actions.MAX_ITEMS
    for i in items:
        actions.validate(i.action_code, i.params, plan)  # never raises: no no-ops, no load increases


def test_at_the_lightest_plan_the_engine_offers_rest_not_an_empty_screen():
    items = diagnosis.build_items("too_hard", PlanState(10, 1, 3), stats(), TODAY)
    assert [i.action_code for i in items] == ["take_rest"]


def test_no_time_uses_the_minutes_the_learner_has():
    items = diagnosis.build_items("no_time", PlanState(30, 5, 8), stats(), TODAY, available_minutes=10)
    assert items[0].action_code == "change_daily_minutes" and items[0].params == {"value": 10}
    assert items[1].action_code == "change_exercise_count" and items[1].params == {"value": 4}  # the 10-minute day (onboarding table)
    assert items[2].action_code == "reduce_new_grammar" and items[2].params == {"value": 1}


def test_no_time_still_offers_something_real_after_the_minutes_were_already_lowered():
    """Regression (review F07): once minutes were at the target the old ratio-based items all became no-ops and the engine
    offered only a rest with the false evidence 'your plan is already at its lightest'."""
    plan = PlanState(10, 5, 8)
    items = diagnosis.build_items("no_time", plan, stats(), TODAY, available_minutes=10)
    assert [i.action_code for i in items] == ["change_exercise_count", "reduce_new_grammar"]
    assert all(e["type"] != "plan_at_minimum" for i in items for e in i.evidence)


def test_lightest_claim_is_only_made_when_it_is_literally_true():
    not_lightest = diagnosis.build_items("distracted", PlanState(10, 5, 8, sessions_per_day=2, focus_minutes=5), stats(), TODAY)
    assert [i.action_code for i in not_lightest] == ["take_rest"]
    assert all(e["type"] != "plan_at_minimum" for e in not_lightest[0].evidence)
    lightest = diagnosis.build_items("too_hard", PlanState(10, 1, 3), stats(), TODAY)
    assert {"type": "plan_at_minimum"} in lightest[0].evidence


def test_need_rest_changes_nothing_but_rest():
    items = diagnosis.build_items("need_rest", PlanState(30, 5, 8), stats(), TODAY)
    assert [(i.action_code, i.params) for i in items] == [("take_rest", {"days": 1})]


def test_distracted_never_tightens_anything():
    items = diagnosis.build_items("distracted", PlanState(30, 5, 8), stats(), TODAY)
    assert {i.action_code for i in items} <= {"pomodoro", "split_session"}


# ----------------------------------------------------------------------------- levels
def test_default_plans_match_the_original_demo_profiles():
    assert (levels.default_plan(30).new_grammar, levels.default_plan(30).exercise_count) == (5, 8)
    assert (levels.default_plan(20).new_grammar, levels.default_plan(20).exercise_count) == (3, 6)
    assert (levels.default_plan(15).new_grammar, levels.default_plan(15).exercise_count) == (2, 5)
    with pytest.raises(ValueError):
        levels.default_plan(31)


def test_placement():
    a1 = [("A1", True)] * 8 + [("A2", False)] * 2
    a2 = [("A1", True)] * 8 + [("A2", True)] * 2
    assert levels.estimate_level(a1) == "A1" and levels.estimate_level(a2) == "A2"
    assert levels.estimate_level([("A1", True)] * 10) == "A1"  # no A2 evidence -> stay A1
    assert levels.estimate_level([]) == "A1"


# ----------------------------------------------------------------------------- scheduler
def _pool():
    qs = [QInfo(f"G{i}", "grammar", "了" if i < 3 else "在", "A1") for i in range(10)]
    qs += [QInfo(f"V{i}", "vocab", None, "A1") for i in range(10)]
    return qs


def test_task_is_deterministic_and_respects_counts():
    a = pick_questions(_pool(), {}, 8, 5, [], user_id=1, day=TODAY)
    b = pick_questions(_pool(), {}, 8, 5, [], user_id=1, day=TODAY)
    assert a == b and len(a) == 8 == len(set(a))
    assert sum(1 for q in a if q.startswith("G")) >= 5  # five NEW grammar items
    assert a != pick_questions(_pool(), {}, 8, 5, [], user_id=2, day=TODAY)


def test_task_prioritises_review_and_wrong_answers():
    hist = {"V3": History(1, False, "2026-11-17"), "G1": History(1, False, "2026-11-17")}
    task = pick_questions(_pool(), hist, 6, 2, ["了"], user_id=1, day=TODAY)
    assert {"G0", "G1", "G2"} & set(task) and "V3" in task


def test_small_bank_never_runs_dry():
    pool = _pool()[:4]
    hist = {q.id: History(3, True, f"2026-11-1{i}") for i, q in enumerate(pool)}
    task = pick_questions(pool, hist, 8, 5, [], user_id=1, day=TODAY)
    assert sorted(task) == sorted(q.id for q in pool)  # asked for 8, bank has 4: all 4, no duplicates
