"""Numbers for the admin dashboard. Pure read-only queries: nothing here writes, nothing is typed in by hand.

Honesty rules (the dashboard follows them, the tests check them):
* every average and every share carries the number of rows (n) behind it;
* with no rows the value is None ("no data yet"), never 0 or 0%;
* interruptions created by the demo simulator (source "simulated") are not counted: pilot metrics describe real learners only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..domain.actions import REASONS
from ..models import Feedback, Interruption, Payment, Question, QuizAttempt, Recommendation, Subscription, User
from ..timeutil import utcnow

INTERRUPTION_STATUSES = ("proposed", "accepted", "declined", "resumed")
PROPOSAL_STATUSES = ("accepted", "partial", "rejected", "superseded", "pending")
REVIEW_STATUSES = ("approved", "pending_review", "needs_edit")
SUBSCRIPTION_STATES = ("trialing", "active", "expired", "none")
RATING_TARGET = 4.0  # finals deck: clarity and helpfulness should average at least 4 out of 5


@dataclass(frozen=True)
class Average:
    n: int
    value: float | None  # None when n == 0

    @property
    def meets_target(self) -> bool | None:
        return None if self.value is None else self.value >= RATING_TARGET


@dataclass(frozen=True)
class Share:
    part: int
    total: int

    @property
    def value(self) -> float | None:  # 0..1, None when there is nothing to divide by
        return self.part / self.total if self.total else None

    @property
    def n(self) -> int:
        return self.total


@dataclass
class Dashboard:
    learners_total: int = 0
    learners_active_7d: int = 0
    interruptions_total: int = 0
    interruptions_by_reason: dict[str, int] = field(default_factory=dict)
    interruptions_by_status: dict[str, int] = field(default_factory=dict)
    proposals_total: int = 0
    proposals_by_status: dict[str, int] = field(default_factory=dict)
    return_rate: Share = Share(0, 0)  # resumed / all interruptions
    clarity: Average = Average(0, None)
    helpfulness: Average = Average(0, None)
    pressure: Average = Average(0, None)
    accepted_share: Share = Share(0, 0)  # of rated proposals, accepted at least partly
    question_bank: dict[str, int] = field(default_factory=dict)
    subscriptions: dict[str, int] = field(default_factory=dict)


def _actual():
    return Interruption.source == "actual"


def learner_counts(db: Session, now: datetime | None = None) -> tuple[int, int]:
    """(all learners, learners who answered at least one question in the last 7 days)."""
    now = now or utcnow()
    total = db.scalar(select(func.count()).select_from(User).where(User.role == "learner")) or 0
    active = db.scalar(select(func.count(func.distinct(QuizAttempt.user_id))).join(User, User.id == QuizAttempt.user_id)
                       .where(User.role == "learner", QuizAttempt.created_at >= now - timedelta(days=7))) or 0
    return total, active


def interruption_counts(db: Session) -> tuple[dict[str, int], dict[str, int]]:
    """(by reason, by status); every known key is present, with 0 when nobody reported it."""
    by_reason = dict.fromkeys(REASONS, 0)
    by_status = dict.fromkeys(INTERRUPTION_STATUSES, 0)
    for reason, status, n in db.execute(select(Interruption.reason_code, Interruption.status, func.count())
                                        .where(_actual()).group_by(Interruption.reason_code, Interruption.status)):
        if reason in by_reason:
            by_reason[reason] += n
        if status in by_status:
            by_status[status] += n
    return by_reason, by_status


def proposal_counts(db: Session) -> dict[str, int]:
    out = dict.fromkeys(PROPOSAL_STATUSES, 0)
    for status, n in db.execute(select(Recommendation.status, func.count())
                                .join(Interruption, Interruption.id == Recommendation.interruption_id)
                                .where(_actual()).group_by(Recommendation.status)):
        if status in out:
            out[status] += n
    return out


def pilot_ratings(db: Session) -> tuple[Average, Average, Average, Share]:
    """clarity, helpfulness, pressure averages (1-5, with n) and the share of rated proposals accepted at least partly."""
    base = (select(Feedback).join(Recommendation, Recommendation.id == Feedback.recommendation_id)
            .join(Interruption, Interruption.id == Recommendation.interruption_id).where(_actual()).subquery())
    n, clarity, helpful, pressure = db.execute(select(func.count(), func.avg(base.c.clarity), func.avg(base.c.helpfulness),
                                                      func.avg(base.c.pressure)).select_from(base)).one()

    def avg(value) -> Average:  # noqa: ANN001
        return Average(n, None if n == 0 or value is None else round(float(value), 2))

    accepted = db.scalar(select(func.count()).select_from(Feedback)
                         .join(Recommendation, Recommendation.id == Feedback.recommendation_id)
                         .join(Interruption, Interruption.id == Recommendation.interruption_id)
                         .where(_actual(), Recommendation.status.in_(("accepted", "partial")))) or 0
    return avg(clarity), avg(helpful), avg(pressure), Share(accepted, n)


def question_bank(db: Session) -> dict[str, int]:
    out = dict.fromkeys(REVIEW_STATUSES, 0)
    for status, n in db.execute(select(Question.review_status, func.count()).group_by(Question.review_status)):
        if status in out:
            out[status] += n
    out["total"] = sum(out.values())
    return out


def subscription_counts(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Learners by the state of their latest subscription (same rule as services.billing.access, ignoring the pilot switch)."""
    now = now or utcnow()
    rows = db.execute(select(Subscription.user_id, Subscription.status, Subscription.current_period_end)
                      .join(User, User.id == Subscription.user_id).where(User.role == "learner")).all()
    latest: dict[int, tuple[str, datetime]] = {}
    for user_id, status, end in rows:
        if user_id not in latest or end > latest[user_id][1]:
            latest[user_id] = (status, end)
    out = dict.fromkeys(SUBSCRIPTION_STATES, 0)
    for status, end in latest.values():
        out[status if status in ("trialing", "active") and end > now else "expired"] += 1
    total = db.scalar(select(func.count()).select_from(User).where(User.role == "learner")) or 0
    out["none"] = max(total - len(latest), 0)
    return out


def dashboard(db: Session, now: datetime | None = None) -> Dashboard:
    now = now or utcnow()
    by_reason, by_status = interruption_counts(db)
    proposals = proposal_counts(db)
    clarity, helpfulness, pressure, accepted = pilot_ratings(db)
    total_i = sum(by_status.values())
    learners, active = learner_counts(db, now)
    return Dashboard(
        learners_total=learners, learners_active_7d=active,
        interruptions_total=total_i, interruptions_by_reason=by_reason, interruptions_by_status=by_status,
        proposals_total=sum(proposals.values()), proposals_by_status=proposals,
        return_rate=Share(by_status["resumed"], total_i),
        clarity=clarity, helpfulness=helpfulness, pressure=pressure, accepted_share=accepted,
        question_bank=question_bank(db), subscriptions=subscription_counts(db, now))


def monthly_revenue(db: Session, months: int = 12, now: datetime | None = None) -> list[tuple[str, int, int]]:
    """[(YYYY-MM, total TWD, number of payments)] newest month first, in the business time zone. Months without payments are omitted."""
    now = now or utcnow()
    tz = ZoneInfo(get_settings().timezone)
    first = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    totals: dict[str, list[int]] = {}
    for created, amount in db.execute(select(Payment.created_at, Payment.amount_twd).where(Payment.created_at >= first)):
        bucket = totals.setdefault(created.astimezone(tz).strftime("%Y-%m"), [0, 0])
        bucket[0] += amount
        bucket[1] += 1
    return [(m, v[0], v[1]) for m, v in sorted(totals.items(), reverse=True)]
