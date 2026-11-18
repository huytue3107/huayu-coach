"""The only things a proposal may do to a learner's plan, and the rules that keep it safe.

Safety rules enforced here (the old Apps Script only range-checked, so a bad proposal could
RAISE the workload; here every numeric action must strictly lower it):

* no action can change the learner's mode (nothing can "tighten discipline");
* reductions only, within hard floors;
* rest and review are explicit actions, never inferred from inactivity.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

REASONS = ("too_hard", "no_time", "need_rest", "distracted")

FLOORS = {"daily_minutes": 10, "new_grammar": 1, "exercise_count": 3}
CEILINGS = {"daily_minutes": 90, "new_grammar": 10, "exercise_count": 20}
MAX_ITEMS = 3
FAR_FUTURE = date(9999, 12, 31)  # "paused until I come back"

ACTION_CODES = (
    "reduce_new_grammar", "change_exercise_count", "change_daily_minutes",
    "split_session", "pomodoro", "take_rest", "review_topic",
)


class ActionError(ValueError):
    """Raised with a stable machine code in args[0]."""


@dataclass(frozen=True)
class PlanState:
    daily_minutes: int = 30
    new_grammar: int = 5
    exercise_count: int = 8
    sessions_per_day: int = 1
    focus_minutes: int | None = None
    review_topics: tuple[tuple[str, str], ...] = ()  # (topic, until-iso-date)


@dataclass(frozen=True)
class ItemSpec:
    action_code: str
    params: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Effect:
    """What applying items to a plan changed."""
    plan: PlanState
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # field -> (old, new)
    rest_until: date | None = None

    @property
    def plan_changed(self) -> bool:
        return any(k != "rest_until" for k in self.changed)


def _int(params: dict[str, Any], key: str) -> int:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ActionError(f"invalid_{key}")
    return v


def validate(action_code: str, params: dict[str, Any], plan: PlanState) -> None:
    """Raise ActionError unless this item is safe to apply to `plan`."""
    if action_code == "reduce_new_grammar":
        v = _int(params, "value")
        if not FLOORS["new_grammar"] <= v < plan.new_grammar:
            raise ActionError("must_reduce_new_grammar")
    elif action_code == "change_exercise_count":
        v = _int(params, "value")
        if not FLOORS["exercise_count"] <= v < plan.exercise_count:
            raise ActionError("must_reduce_exercise_count")
    elif action_code == "change_daily_minutes":
        v = _int(params, "value")
        if not FLOORS["daily_minutes"] <= v < plan.daily_minutes:
            raise ActionError("must_reduce_daily_minutes")
    elif action_code == "split_session":
        v = _int(params, "sessions")
        if not (2 <= v <= 4 and v > plan.sessions_per_day):
            raise ActionError("invalid_split_session")
    elif action_code == "pomodoro":
        v = _int(params, "focus_minutes")
        if not (5 <= v <= 25) or (plan.focus_minutes is not None and v >= plan.focus_minutes):
            raise ActionError("invalid_pomodoro")
    elif action_code == "take_rest":
        v = _int(params, "days")
        if not 1 <= v <= 7:
            raise ActionError("invalid_rest_days")
    elif action_code == "review_topic":
        topic = params.get("topic")
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 30:
            raise ActionError("invalid_topic")
    else:
        raise ActionError("unknown_action_code")


def apply(action_code: str, params: dict[str, Any], plan: PlanState, today: date,
          effect: Effect | None = None) -> Effect:
    """Validate then apply one item on top of `effect` (or a fresh Effect for `plan`)."""
    effect = effect or Effect(plan=plan)
    cur = effect.plan
    validate(action_code, params, cur)
    new = cur
    changes: dict[str, tuple[Any, Any]] = {}

    def set_(name: str, value: Any) -> None:
        nonlocal new
        old = getattr(cur, name)
        if old != value:
            new = replace(new, **{name: value})
            first_old = effect.changed.get(name, (old, None))[0]
            changes[name] = (first_old, value)

    if action_code == "reduce_new_grammar":
        set_("new_grammar", params["value"])
    elif action_code == "change_exercise_count":
        set_("exercise_count", params["value"])
    elif action_code == "change_daily_minutes":
        set_("daily_minutes", params["value"])
    elif action_code == "split_session":
        set_("sessions_per_day", params["sessions"])
    elif action_code == "pomodoro":
        set_("focus_minutes", params["focus_minutes"])
    elif action_code == "review_topic":
        topic = params["topic"].strip()
        until = (today + timedelta(days=7)).isoformat()
        others = tuple(t for t in cur.review_topics if t[0] != topic)
        set_("review_topics", (*others, (topic, until))[-3:])
    out = Effect(plan=new, changed={**effect.changed, **changes}, rest_until=effect.rest_until)
    if action_code == "take_rest":
        out.rest_until = today + timedelta(days=params["days"])
        out.changed["rest_until"] = (None, out.rest_until.isoformat())
    return out
