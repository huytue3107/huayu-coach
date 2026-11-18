"""Access control by subscription. With HUAYU_BILLING_ENABLED=false everyone has free access (pilot mode).

Payments are manual at first: the learner pays by bank transfer / any local method, an admin records it
with `activate()`. A real gateway can later call the same `activate()` from its webhook.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuditLog, BillingPlan, Payment, Subscription, User
from ..timeutil import utcnow

# Placeholder prices: the owner sets the real ones in the admin area / seeds/plans.json.
DEFAULT_PLANS = [
    {"code": "monthly", "price_twd": 199, "period_days": 30,
     "name": {"vi": "Theo tháng", "zh-TW": "月方案", "en": "Monthly"}},
    {"code": "semester", "price_twd": 799, "period_days": 150,
     "name": {"vi": "Theo học kỳ (5 tháng)", "zh-TW": "學期方案（5 個月）", "en": "Semester (5 months)"}},
]


@dataclass(frozen=True)
class Access:
    state: str  # free | staff | trialing | active | expired
    until: datetime | None = None

    @property
    def allowed(self) -> bool:
        return self.state != "expired"


def seed_plans(db: Session) -> int:
    added = 0
    for p in DEFAULT_PLANS:
        if db.get(BillingPlan, p["code"]) is None:
            db.add(BillingPlan(code=p["code"], name=p["name"], price_twd=p["price_twd"], period_days=p["period_days"]))
            added += 1
    return added


def latest_subscription(db: Session, user_id: int) -> Subscription | None:
    return db.scalars(select(Subscription).where(Subscription.user_id == user_id)
                      .order_by(Subscription.current_period_end.desc())).first()


def access(db: Session, user: User, now: datetime | None = None) -> Access:
    now = now or utcnow()
    if user.role in ("admin", "teacher"):
        return Access("staff")
    if not get_settings().billing_enabled:
        return Access("free")
    sub = latest_subscription(db, user.id)
    if sub and sub.status in ("trialing", "active") and sub.current_period_end > now:
        return Access(sub.status, sub.current_period_end)
    return Access("expired", sub.current_period_end if sub else None)


def start_trial(db: Session, user: User, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    sub = Subscription(user_id=user.id, plan_code=None, status="trialing", provider="trial", started_at=now,
                       current_period_end=now + timedelta(days=get_settings().trial_days))
    db.add(sub)
    db.flush()
    return sub


def activate(db: Session, user: User, plan_code: str, actor: User | None = None, note: str | None = None,
             amount_twd: int | None = None, provider: str = "manual", provider_ref: str | None = None,
             now: datetime | None = None) -> Subscription:
    """Record a payment and extend access. Extends from the current end if the learner is still covered."""
    now = now or utcnow()
    plan = db.get(BillingPlan, plan_code)
    if plan is None or not plan.active:
        raise ValueError("unknown_plan")
    cur = latest_subscription(db, user.id)
    start = cur.current_period_end if cur and cur.current_period_end > now else now
    sub = Subscription(user_id=user.id, plan_code=plan.code, status="active", provider=provider,
                       provider_ref=provider_ref, started_at=now, current_period_end=start + timedelta(days=plan.period_days))
    db.add(sub)
    db.flush()
    db.add(Payment(user_id=user.id, subscription_id=sub.id, amount_twd=plan.price_twd if amount_twd is None else amount_twd,
                   provider=provider, provider_ref=provider_ref, note=note))
    db.add(AuditLog(actor_id=actor.id if actor else None, action="subscription_activated", target=user.public_id,
                    detail={"plan": plan.code, "until": sub.current_period_end.isoformat(), "note": note}))
    return sub


def grant_days(db: Session, user: User, days: int, actor: User | None = None, note: str | None = None,
               now: datetime | None = None) -> Subscription:
    """Give free access (support gesture, extended trial). Not a payment."""
    now = now or utcnow()
    cur = latest_subscription(db, user.id)
    start = cur.current_period_end if cur and cur.current_period_end > now else now
    sub = Subscription(user_id=user.id, plan_code=None, status="trialing", provider="grant", started_at=now,
                       current_period_end=start + timedelta(days=days))
    db.add(sub)
    db.flush()
    db.add(AuditLog(actor_id=actor.id if actor else None, action="access_granted", target=user.public_id,
                    detail={"days": days, "note": note}))
    return sub


DUPLICATE_WINDOW = timedelta(seconds=60)


def recent_duplicate_payment(db: Session, user: User, plan_code: str, amount_twd: int | None,
                             now: datetime | None = None) -> bool:
    """True if the same payment was already recorded in the last minute (a double click / double submit)."""
    plan = db.get(BillingPlan, plan_code)
    if plan is None:
        return False
    amount = plan.price_twd if amount_twd is None else amount_twd
    since = (now or utcnow()) - DUPLICATE_WINDOW
    return db.scalars(select(Payment).join(Subscription, Payment.subscription_id == Subscription.id)
                      .where(Payment.user_id == user.id, Payment.amount_twd == amount, Subscription.plan_code == plan_code,
                             Payment.created_at >= since)).first() is not None


def recent_duplicate_grant(db: Session, user: User, days: int, now: datetime | None = None) -> bool:
    since = (now or utcnow()) - DUPLICATE_WINDOW
    for row in db.scalars(select(AuditLog).where(AuditLog.action == "access_granted", AuditLog.target == user.public_id,
                                                 AuditLog.created_at >= since)):
        if (row.detail or {}).get("days") == days:
            return True
    return False
