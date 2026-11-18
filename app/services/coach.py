"""The heart of the product: interruption -> evidence-based proposal -> learner decides -> plan changes.

Guarantees (each one is covered by tests/test_coach.py):
* creating a proposal never touches the plan; only `decide(..., "accept")` does;
* a decision is one transaction: plan, items, recommendation, interruption and audit row commit together or not at all;
* a proposal computed from plan version N can only be applied while the plan is still at version N;
* every numeric item must lower the load; nothing can change the learner's mode;
* deciding twice is refused ("already_processed"), so a double tap cannot apply a change twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain import actions
from ..domain.diagnosis import build_items
from ..models import (Feedback, Interruption, PlanChange, Recommendation, SuggestionItem, User)
from ..timeutil import local_today, utcnow
from .plans import CoachError, snapshot, to_state, write_effect
from .stats import learner_stats

MAX_SELF_REPORT = 500
MAX_REPORTS_PER_DAY = 20


@dataclass
class DecisionResult:
    status: str  # accepted | partial | rejected
    recommendation: Recommendation
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    plan_changed: bool = False
    old_version: int = 0
    new_version: int = 0
    rest_until: date | None = None


def clean_text(text: str | None, limit: int = MAX_SELF_REPORT) -> str | None:
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch == "\n" or ch >= " ").strip()
    return cleaned[:limit] or None


def pending_recommendation(db: Session, user: User) -> Recommendation | None:
    return db.scalars(select(Recommendation).where(Recommendation.user_id == user.id, Recommendation.status == "pending")
                      .order_by(Recommendation.id.desc())).first()


def report_interruption(db: Session, user: User, reason: str, self_report: str | None = None,
                        available_minutes: int | None = None, now: datetime | None = None) -> Recommendation:
    """Record WHY the learner stopped (their words, never inferred) and build a proposal. Changes no plan."""
    now = now or utcnow()
    if reason not in actions.REASONS:
        raise CoachError("invalid_reason")
    if user.plan is None:
        raise CoachError("no_plan")
    if available_minutes is not None and not (isinstance(available_minutes, int) and 5 <= available_minutes <= 240):
        raise CoachError("invalid_available_minutes")
    since = now - timedelta(days=1)
    recent = db.scalar(select(func.count()).select_from(Interruption).where(Interruption.user_id == user.id,
                                                                            Interruption.created_at >= since))
    if recent >= MAX_REPORTS_PER_DAY:
        raise CoachError("too_many_reports")

    today = local_today(user.timezone, now)
    for old in db.scalars(select(Recommendation).where(Recommendation.user_id == user.id, Recommendation.status == "pending")):
        old.status = "superseded"

    plan = user.plan
    state = to_state(plan, today)
    stats = learner_stats(db, user, now)
    items = build_items(reason, state, stats, today, available_minutes)

    interruption = Interruption(user_id=user.id, reason_code=reason, self_report=clean_text(self_report),
                                status="proposed", created_at=now,
                                context={"plan": snapshot(plan), "plan_version": plan.version, "stats": stats.as_facts()})
    db.add(interruption)
    db.flush()
    rec = Recommendation(interruption_id=interruption.id, user_id=user.id, base_plan_version=plan.version,
                         observed_facts=stats.as_facts(), engine="rules_v1", status="pending", created_at=now)
    db.add(rec)
    db.flush()
    for spec in items:
        db.add(SuggestionItem(recommendation_id=rec.id, action_code=spec.action_code, params=spec.params,
                              evidence=spec.evidence))
    db.flush()
    db.refresh(rec)
    return rec


def decide(db: Session, user: User, rec_id: int, decision: str, item_ids: list[int] | None = None,
           now: datetime | None = None) -> DecisionResult:
    """Apply the learner's decision. `decision` is "accept" (with the chosen item ids) or "reject"."""
    now = now or utcnow()
    if decision not in ("accept", "reject"):
        raise CoachError("invalid_decision")
    rec = db.get(Recommendation, rec_id)
    if rec is None or rec.user_id != user.id:
        raise CoachError("recommendation_not_found")
    if rec.status != "pending":
        raise CoachError("already_processed")
    plan = user.plan
    if plan is None:
        raise CoachError("no_plan")
    interruption = rec.interruption
    old_version = plan.version

    if decision == "reject":
        for item in rec.items:
            item.status, item.decided_at = "rejected", now
        rec.status, rec.decided_at = "rejected", now
        if interruption.resumed_at is None:  # keep "resumed" if the learner already came back
            interruption.status = "declined"
        db.add(PlanChange(user_id=user.id, source="proposal", interruption_id=interruption.id, recommendation_id=rec.id,
                          result="reject", old_values={}, new_values={}, old_version=old_version, new_version=old_version))
        db.flush()
        return DecisionResult("rejected", rec, old_version=old_version, new_version=old_version)

    ids = list(item_ids or [])
    if not ids:
        raise CoachError("choose_items")
    if len(ids) != len(set(ids)):
        raise CoachError("duplicate_item")
    by_id = {i.id: i for i in rec.items}
    if any(i not in by_id for i in ids):
        raise CoachError("item_not_in_recommendation")
    if plan.version != rec.base_plan_version:
        raise CoachError("stale_plan")  # the plan moved on since this proposal was computed

    today = local_today(user.timezone, now)
    effect = actions.Effect(plan=to_state(plan, today))
    try:
        for item in rec.items:  # stable order, not the order the client sent
            if item.id in ids:
                effect = actions.apply(item.action_code, item.params, effect.plan, today, effect)
    except actions.ActionError as exc:
        raise CoachError(f"invalid_item:{exc.args[0]}") from exc

    new_version = write_effect(plan, effect)
    if effect.rest_until and (user.paused_until is None or user.paused_until < effect.rest_until):
        user.paused_until = effect.rest_until  # accepting a 1-day rest must never shorten a longer pause the learner chose
    for item in rec.items:
        item.status = "accepted" if item.id in ids else "rejected"
        item.decided_at = now
    rec.status = "accepted" if len(ids) == len(rec.items) else "partial"
    rec.decided_at = now
    if interruption.resumed_at is None:
        interruption.status = "accepted"
    db.add(PlanChange(user_id=user.id, source="proposal", interruption_id=interruption.id, recommendation_id=rec.id,
                      result="accept" if rec.status == "accepted" else "partial",
                      old_values={k: v[0] for k, v in effect.changed.items()},
                      new_values={k: v[1] for k, v in effect.changed.items()},
                      old_version=old_version, new_version=new_version))
    db.flush()
    return DecisionResult(rec.status, rec, changed=dict(effect.changed), plan_changed=effect.plan_changed,
                          old_version=old_version, new_version=new_version, rest_until=effect.rest_until)


def mark_resumed(db: Session, user: User, now: datetime | None = None) -> int:
    """Call when the learner finishes studying. Interruptions from the last 7 days become "resumed",
    which is the signal for "did the learner come back after an interruption?"."""
    now = now or utcnow()
    rows = db.scalars(select(Interruption).where(
        Interruption.user_id == user.id, Interruption.status.in_(("proposed", "accepted", "declined")),
        Interruption.created_at >= now - timedelta(days=7))).all()
    for row in rows:
        row.status, row.resumed_at = "resumed", now
    return len(rows)


def save_feedback(db: Session, user: User, rec_id: int, clarity: int, helpfulness: int, pressure: int,
                  comment: str | None = None) -> Feedback:
    rec = db.get(Recommendation, rec_id)
    if rec is None or rec.user_id != user.id:
        raise CoachError("recommendation_not_found")
    if rec.status == "pending":
        raise CoachError("decide_first")
    if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5 for v in (clarity, helpfulness, pressure)):
        raise CoachError("invalid_rating")
    if db.scalar(select(func.count()).select_from(Feedback).where(Feedback.recommendation_id == rec_id)):
        raise CoachError("already_rated")
    fb = Feedback(user_id=user.id, recommendation_id=rec_id, clarity=clarity, helpfulness=helpfulness,
                  pressure=pressure, comment=clean_text(comment))
    db.add(fb)
    db.flush()
    return fb


def pause(db: Session, user: User, days: int | None, now: datetime | None = None) -> date:
    """Emergency pause / rest. `days=None` means "until I come back". Never blocked by anything."""
    today = local_today(user.timezone, now)
    if days is not None and not 1 <= days <= 30:
        raise CoachError("invalid_rest_days")
    user.paused_until = actions.FAR_FUTURE if days is None else today + timedelta(days=days)
    return user.paused_until


def resume(db: Session, user: User) -> None:
    user.paused_until = None


def is_paused(user: User, now: datetime | None = None) -> bool:
    return bool(user.paused_until and user.paused_until > local_today(user.timezone, now))
