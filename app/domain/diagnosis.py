"""Rule engine: reason + plan + what the data really shows  ->  at most three adjustments, each with evidence.

The engine decides every number. An LLM may later re-word the explanation, but it can never change
or add a number (see services/llm.py). Evidence is structured data, rendered per language by the UI;
a percentage is only quoted when there are enough attempts behind it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .actions import FLOORS, MAX_ITEMS, ItemSpec, PlanState, validate, ActionError
from .levels import PLAN_PRESETS

MIN_ATTEMPTS_FOR_PERCENT = 5
WEAK_TOPIC_MIN_WRONG = 2


@dataclass
class LearnerStats:
    grammar_correct: int = 0
    grammar_total: int = 0
    vocab_correct: int = 0
    vocab_total: int = 0
    weak_topics: list[tuple[str, int]] = field(default_factory=list)  # (topic, wrong count), most wrong first
    tasks_done_3d: int = 0
    tasks_scheduled_3d: int = 0
    window_days: int = 14

    def as_facts(self) -> dict[str, Any]:
        return {
            "grammar": {"correct": self.grammar_correct, "total": self.grammar_total},
            "vocab": {"correct": self.vocab_correct, "total": self.vocab_total},
            "weak_topics": [{"topic": t, "wrong": n} for t, n in self.weak_topics[:3]],
            "tasks_3d": {"done": self.tasks_done_3d, "scheduled": self.tasks_scheduled_3d},
            "window_days": self.window_days,
        }


def _accuracy_fact(stats: LearnerStats) -> dict[str, Any]:
    if stats.grammar_total >= MIN_ATTEMPTS_FOR_PERCENT:
        return {"type": "accuracy", "skill": "grammar", "correct": stats.grammar_correct,
                "total": stats.grammar_total, "days": stats.window_days}
    return {"type": "few_data", "skill": "grammar", "total": stats.grammar_total}


def _completion_fact(stats: LearnerStats) -> list[dict[str, Any]]:
    if stats.tasks_scheduled_3d >= 2:
        return [{"type": "completion", "done": stats.tasks_done_3d, "scheduled": stats.tasks_scheduled_3d, "days": 3}]
    return []


def _plan_fact(field_name: str, value: int) -> dict[str, Any]:
    return {"type": "plan_value", "field": field_name, "value": value}


def _round5(n: float) -> int:
    return int(round(n / 5.0) * 5)


def _preset_for(minutes: int) -> tuple[int, int]:
    """(new grammar, exercises) the app itself uses for a day of about this length (same table as onboarding)."""
    key = max((k for k in PLAN_PRESETS if k <= minutes), default=min(PLAN_PRESETS))
    return PLAN_PRESETS[key]


def build_items(reason: str, plan: PlanState, stats: LearnerStats, today: date,
                available_minutes: int | None = None) -> list[ItemSpec]:
    """Return 0..3 valid items. Every item is validated against `plan`, so the engine can never emit a no-op
    or a workload increase."""
    self_report = {"type": "self_report", "reason": reason}
    items: list[ItemSpec] = []

    if reason == "too_hard":
        g = max(FLOORS["new_grammar"], math.ceil(plan.new_grammar * 0.4))
        items.append(ItemSpec("reduce_new_grammar", {"value": g},
                              [self_report, _plan_fact("new_grammar", plan.new_grammar), _accuracy_fact(stats)]))
        e = max(FLOORS["exercise_count"], round(plan.exercise_count * 0.6))
        items.append(ItemSpec("change_exercise_count", {"value": e},
                              [self_report, _plan_fact("exercise_count", plan.exercise_count)]))
        if stats.weak_topics and stats.weak_topics[0][1] >= WEAK_TOPIC_MIN_WRONG:
            topic, wrong = stats.weak_topics[0]
            items.append(ItemSpec("review_topic", {"topic": topic},
                                  [self_report, {"type": "weak_topic", "topic": topic, "wrong": wrong,
                                                 "days": stats.window_days}]))
        if not any(_ok(i, plan) for i in items):  # plan already at the lightest setting
            m = max(FLOORS["daily_minutes"], plan.daily_minutes - 10)
            items.append(ItemSpec("change_daily_minutes", {"value": m},
                                  [self_report, _plan_fact("daily_minutes", plan.daily_minutes)]))

    elif reason == "no_time":
        target = _round5(available_minutes) if available_minutes else max(FLOORS["daily_minutes"], _round5(plan.daily_minutes / 2))
        target = max(FLOORS["daily_minutes"], min(target, plan.daily_minutes - 5))
        ev = [self_report, _plan_fact("daily_minutes", plan.daily_minutes), *_completion_fact(stats)]
        if available_minutes:
            ev.append({"type": "available_minutes", "value": available_minutes})
        items.append(ItemSpec("change_daily_minutes", {"value": target}, ev))
        # size the day for the shorter time with the same table onboarding uses, so the items stay meaningful even when
        # the minutes were already lowered earlier (a ratio of the CURRENT plan collapsed to "no change")
        g, e = _preset_for(target)
        items.append(ItemSpec("change_exercise_count", {"value": max(FLOORS["exercise_count"], e)},
                              [self_report, _plan_fact("exercise_count", plan.exercise_count)]))
        items.append(ItemSpec("reduce_new_grammar", {"value": max(FLOORS["new_grammar"], g)},
                              [self_report, _plan_fact("new_grammar", plan.new_grammar)]))
        if plan.daily_minutes >= 20 and plan.sessions_per_day == 1:
            items.append(ItemSpec("split_session", {"sessions": 2}, [self_report, *_completion_fact(stats)]))

    elif reason == "need_rest":
        # Rest is respected as stated: one day, no load changes, nothing inferred from activity.
        items.append(ItemSpec("take_rest", {"days": 1}, [self_report]))

    elif reason == "distracted":
        items.append(ItemSpec("pomodoro", {"focus_minutes": 10}, [self_report]))
        if plan.daily_minutes >= 20 and plan.sessions_per_day == 1:
            items.append(ItemSpec("split_session", {"sessions": 2}, [self_report, *_completion_fact(stats)]))

    valid = [i for i in items if _ok(i, plan)]
    if not valid:  # nothing gentler to propose: offer a rest instead of an empty screen
        evidence = [self_report]
        if (plan.daily_minutes <= FLOORS["daily_minutes"] and plan.new_grammar <= FLOORS["new_grammar"]
                and plan.exercise_count <= FLOORS["exercise_count"]):
            evidence.append({"type": "plan_at_minimum"})  # only claim "lightest" when it is literally true
        valid = [ItemSpec("take_rest", {"days": 1}, evidence)]
    return valid[:MAX_ITEMS]


def _ok(item: ItemSpec, plan: PlanState) -> bool:
    try:
        validate(item.action_code, item.params, plan)
        return True
    except ActionError:
        return False
