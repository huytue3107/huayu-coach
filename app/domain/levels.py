"""Placement and default plans. Deliberately simple and explainable; teachers can override a level."""
from __future__ import annotations

from .actions import PlanState

# daily minutes -> (new grammar points, exercises); 15/20/30 are the three profiles in the original demo data
PLAN_PRESETS = {
    10: (1, 4),
    15: (2, 5),
    20: (3, 6),
    30: (5, 8),
    45: (6, 10),
}


def default_plan(daily_minutes: int) -> PlanState:
    if daily_minutes not in PLAN_PRESETS:
        raise ValueError("unsupported daily_minutes")
    g, e = PLAN_PRESETS[daily_minutes]
    return PlanState(daily_minutes=daily_minutes, new_grammar=g, exercise_count=e)


def estimate_level(results: list[tuple[str, bool]]) -> str:
    """results = [(question level, correct?)]. A2 needs a strong total AND every A2 item right, and at least two A2 items
    were asked (one lucky answer is not evidence)."""
    total = len(results)
    correct = sum(1 for _, ok in results if ok)
    a2 = [ok for lvl, ok in results if lvl == "A2"]
    if total and correct / total >= 0.8 and len(a2) >= 2 and all(a2):
        return "A2"
    return "A1"
