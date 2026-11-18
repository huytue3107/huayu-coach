"""Database schema. One row = one fact; every learner-owned table cascades on user deletion
(privacy: "delete my data" removes everything the learner produced)."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .timeutil import utcnow

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_public_id() -> str:
    """Anonymous learner code shown in reports and exports (never the e-mail address)."""
    return "L" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


class UTCDateTime(TypeDecorator):
    """Stores UTC, returns timezone-aware datetimes, refuses naive ones."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timeutil.utcnow()")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime()}


# --------------------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"sqlite_autoincrement": True}  # never reuse the id of a deleted account

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(12), unique=True, default=new_public_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="learner")  # learner | teacher | admin
    locale: Mapped[str] = mapped_column(String(8), default="vi")
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Taipei")
    display_name: Mapped[str | None] = mapped_column(String(60))
    level: Mapped[str | None] = mapped_column(String(2))  # A1 | A2, set by the placement test
    mode: Mapped[str] = mapped_column(String(12), default="balanced")  # encouraging | balanced | active
    paused_until: Mapped[date | None] = mapped_column(Date)  # "I need a rest" — no tasks, no pressure
    session_version: Mapped[int] = mapped_column(Integer, default=1)  # bump = log out everywhere
    onboarded_at: Mapped[datetime | None]
    pretest_done_at: Mapped[datetime | None]
    posttest_done_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None]

    plan: Mapped["StudyPlan | None"] = relationship(back_populates="user", uselist=False,
                                                    cascade="all, delete-orphan", passive_deletes=True)


class Consent(Base):
    """Append-only. The latest row is the current state; withdrawal is a new row, never an edit."""
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(10))
    data_scope: Mapped[str] = mapped_column(String(40), default="quiz_and_activity_only")
    llm_transfer_agreed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


# --------------------------------------------------------------------------- plan
class StudyPlan(Base):
    """The learner's current plan. `version` increases on every change (optimistic concurrency)."""
    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    daily_minutes: Mapped[int] = mapped_column(Integer, default=30)
    new_grammar: Mapped[int] = mapped_column(Integer, default=5)
    exercise_count: Mapped[int] = mapped_column(Integer, default=8)
    sessions_per_day: Mapped[int] = mapped_column(Integer, default=1)
    focus_minutes: Mapped[int | None] = mapped_column(Integer)  # set when pomodoro-style is on
    review_topics: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{"topic": "了", "until": "2026-11-25"}]
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="plan")


class PlanChange(Base):
    """Audit trail: who changed which plan field, from what to what, and why."""
    __tablename__ = "plan_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(12))  # default | learner | proposal
    interruption_id: Mapped[int | None] = mapped_column(Integer)
    recommendation_id: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(10))  # accept | partial | reject | manual | default
    old_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    old_version: Mapped[int] = mapped_column(Integer)
    new_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# --------------------------------------------------------------------------- content
class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # Q001
    stage: Mapped[str] = mapped_column(String(10), index=True)  # pretest | daily | posttest
    day: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(2))  # A1 | A2
    skill: Mapped[str] = mapped_column(String(10))  # vocab | grammar | reading
    topic: Mapped[str | None] = mapped_column(String(30), index=True)  # grammar point, e.g. 了 / 在 / 量詞
    question_zh: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSON)  # exactly 4 strings
    correct_index: Mapped[int] = mapped_column(Integer)
    explanation_vi: Mapped[str | None] = mapped_column(Text)
    explanation_zh: Mapped[str | None] = mapped_column(Text)
    explanation_en: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16), default="pending_review", index=True)
    # pending_review | approved | needs_edit
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(String(60), default="self-authored demo")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    stage: Mapped[str] = mapped_column(String(10))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("study_tasks.id", ondelete="SET NULL"))
    selected_text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    skill: Mapped[str] = mapped_column(String(10))
    topic: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (Index("ix_attempt_user_time", "user_id", "created_at"),)


class StudyTask(Base):
    """One day's work for one learner. Built from the plan that was current that morning."""
    __tablename__ = "study_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    task_date: Mapped[date] = mapped_column(Date)
    day_no: Mapped[int] = mapped_column(Integer)
    question_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    planned_minutes: Mapped[int] = mapped_column(Integer)
    plan_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done | skipped
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None]

    __table_args__ = (UniqueConstraint("user_id", "task_date", name="uq_task_user_day"),)


# --------------------------------------------------------------------------- coaching
class Interruption(Base):
    """A learner told us why they stopped. Only ever created by the learner (never inferred from inactivity)."""
    __tablename__ = "interruptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason_code: Mapped[str] = mapped_column(String(12))  # too_hard | no_time | need_rest | distracted
    self_report: Mapped[str | None] = mapped_column(String(500))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="proposed")
    # proposed -> accepted | declined -> resumed  (resumed = learner studied again after deciding)
    source: Mapped[str] = mapped_column(String(10), default="actual")  # actual | simulated
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resumed_at: Mapped[datetime | None]


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    interruption_id: Mapped[int] = mapped_column(ForeignKey("interruptions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    base_plan_version: Mapped[int] = mapped_column(Integer)  # the plan version the numbers were computed from
    observed_facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    engine: Mapped[str] = mapped_column(String(20), default="rules_v1")
    explanation: Mapped[str | None] = mapped_column(Text)  # optional AI wording; never changes the numbers
    explanation_source: Mapped[str | None] = mapped_column(String(12))  # template | llm
    status: Mapped[str] = mapped_column(String(12), default="pending")
    # pending | accepted | partial | rejected | superseded
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None]

    items: Mapped[list["SuggestionItem"]] = relationship(back_populates="recommendation", order_by="SuggestionItem.id",
                                                         cascade="all, delete-orphan", passive_deletes=True)
    interruption: Mapped[Interruption] = relationship()


class SuggestionItem(Base):
    __tablename__ = "suggestion_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), index=True)
    action_code: Mapped[str] = mapped_column(String(24))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # e.g. {"value": 2}
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)  # structured facts, rendered per locale
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | accepted | rejected
    decided_at: Mapped[datetime | None]

    recommendation: Mapped[Recommendation] = relationship(back_populates="items")


class Feedback(Base):
    """1–5 ratings the learner gives after deciding (feeds the pilot metrics: clarity, helpfulness, pressure)."""
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), unique=True)
    clarity: Mapped[int] = mapped_column(Integer)
    helpfulness: Mapped[int] = mapped_column(Integer)
    pressure: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# --------------------------------------------------------------------------- billing
class BillingPlan(Base):
    __tablename__ = "billing_plans"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # monthly | semester | yearly
    name: Mapped[dict[str, Any]] = mapped_column(JSON)  # {"vi": ..., "zh-TW": ..., "en": ...}
    price_twd: Mapped[int] = mapped_column(Integer)
    period_days: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_code: Mapped[str | None] = mapped_column(ForeignKey("billing_plans.code"))  # null = free trial
    status: Mapped[str] = mapped_column(String(10))  # trialing | active | canceled | expired
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    current_period_end: Mapped[datetime]
    canceled_at: Mapped[datetime | None]


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    amount_twd: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditLog(Base):
    """Who did what in the admin area (and account deletions, which keep only the public code)."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
