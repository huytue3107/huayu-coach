"""Bridge between the StudyPlan row and the pure PlanState used by the domain layer."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ..domain.actions import CEILINGS, FLOORS, Effect, PlanState
from ..domain.levels import default_plan
from ..models import PlanChange, StudyPlan, User
from ..timeutil import utcnow


class CoachError(Exception):
    """Business-rule failure with a stable code the UI can translate (args[0])."""

    @property
    def code(self) -> str:
        return str(self.args[0])


PLAN_FIELDS = ("daily_minutes", "new_grammar", "exercise_count", "sessions_per_day", "focus_minutes", "review_topics")


def to_state(plan: StudyPlan, today: date) -> PlanState:
    topics = tuple((t["topic"], t["until"]) for t in (plan.review_topics or []) if t.get("until", "") >= today.isoformat())
    return PlanState(plan.daily_minutes, plan.new_grammar, plan.exercise_count, plan.sessions_per_day,
                     plan.focus_minutes, topics)


def snapshot(plan: StudyPlan) -> dict[str, Any]:
    return {f: getattr(plan, f) for f in PLAN_FIELDS}


def create_default_plan(db: Session, user: User, daily_minutes: int = 30) -> StudyPlan:
    state = default_plan(daily_minutes)
    plan = StudyPlan(user_id=user.id, daily_minutes=state.daily_minutes, new_grammar=state.new_grammar,
                     exercise_count=state.exercise_count, version=1)
    db.add(plan)
    db.flush()
    db.add(PlanChange(user_id=user.id, source="default", result="default", old_values={}, new_values=snapshot(plan),
                      old_version=0, new_version=1))
    user.plan = plan
    return plan


def write_effect(plan: StudyPlan, effect: Effect) -> int:
    """Persist a computed Effect on the row. Returns the new version (unchanged if only rest changed)."""
    if not effect.plan_changed:
        return plan.version
    s = effect.plan
    plan.daily_minutes, plan.new_grammar, plan.exercise_count = s.daily_minutes, s.new_grammar, s.exercise_count
    plan.sessions_per_day, plan.focus_minutes = s.sessions_per_day, s.focus_minutes
    plan.review_topics = [{"topic": t, "until": u} for t, u in s.review_topics]
    plan.version += 1
    plan.updated_at = utcnow()
    return plan.version


def manual_update(db: Session, user: User, *, daily_minutes: int, new_grammar: int, exercise_count: int,
                  expected_version: int) -> PlanChange:
    """The learner edits their own plan directly (any direction, within hard limits). Pending suggestions
    computed from the old numbers are superseded."""
    from ..models import Recommendation  # local import: avoid a cycle at import time

    plan = user.plan
    if plan is None:
        raise CoachError("no_plan")
    if plan.version != expected_version:
        raise CoachError("stale_plan")
    for name, value in (("daily_minutes", daily_minutes), ("new_grammar", new_grammar), ("exercise_count", exercise_count)):
        if isinstance(value, bool) or not isinstance(value, int) or not FLOORS[name] <= value <= CEILINGS[name]:
            raise CoachError(f"invalid_{name}")
    if new_grammar > exercise_count:
        raise CoachError("invalid_new_grammar")  # a day cannot introduce more new patterns than it has exercises
    old = snapshot(plan)
    if (daily_minutes, new_grammar, exercise_count) == (old["daily_minutes"], old["new_grammar"], old["exercise_count"]):
        raise CoachError("no_change")
    old_version = plan.version
    plan.daily_minutes, plan.new_grammar, plan.exercise_count = daily_minutes, new_grammar, exercise_count
    plan.version += 1
    plan.updated_at = utcnow()
    for rec in db.query(Recommendation).filter_by(user_id=user.id, status="pending"):
        rec.status = "superseded"
    change = PlanChange(user_id=user.id, source="learner", result="manual", old_values=old, new_values=snapshot(plan),
                        old_version=old_version, new_version=plan.version)
    db.add(change)
    return change
