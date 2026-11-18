"""Which questions may be served to learners.

* `needs_edit` is NEVER served (a teacher flagged it as ambiguous or wrong).
* `approved` is always served.
* `pending_review` is served only while HUAYU_ALLOW_UNREVIEWED_QUESTIONS is true (default in dev/test and for a pilot;
  default false in prod, so a paid product only ever shows teacher-approved content).
"""
from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Question


def servable_statuses() -> tuple[str, ...]:
    return ("approved", "pending_review") if get_settings().allow_unreviewed_questions else ("approved",)


def pool(db: Session, stage: str, level: str | None = None) -> list[Question]:
    """Servable questions of a stage. An A1 learner gets A1 items; an A2 (or unknown) learner gets A1 + A2."""
    stmt = select(Question).where(Question.stage == stage, Question.active.is_(True),
                                  Question.review_status.in_(servable_statuses())).order_by(Question.id)
    if level == "A1":
        stmt = stmt.where(Question.level == "A1")
    return list(db.scalars(stmt))


def shuffled_options(question: Question, user_id: int, nonce: int) -> list[str]:
    """Deterministic per (user, question, nonce): the page and the answer check recompute the same order, so the server
    needs no per-question state, and the correct answer is not always in the same slot."""
    rng = random.Random(f"{user_id}:{question.id}:{nonce}")
    opts = list(question.options)
    rng.shuffle(opts)
    return opts


def localized_explanation(question: Question, locale: str) -> str | None:
    order = {"vi": ("explanation_vi", "explanation_zh", "explanation_en"),
             "zh-TW": ("explanation_zh", "explanation_vi", "explanation_en"),
             "en": ("explanation_en", "explanation_vi", "explanation_zh")}[locale if locale in ("vi", "zh-TW", "en") else "vi"]
    for name in order:
        if (text := getattr(question, name)):
            return text
    return None
