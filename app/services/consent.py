"""Consent is append-only (see models.Consent). These helpers read the current state."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Consent, User

CONSENT_VERSION = "v1"


def current(db: Session, user: User) -> Consent | None:
    return db.scalars(select(Consent).where(Consent.user_id == user.id).order_by(Consent.id.desc())).first()


def llm_allowed(db: Session, user: User) -> bool:
    """True only if the learner explicitly agreed that structured study data may be sent to an AI provider."""
    c = current(db, user)
    return bool(c and c.llm_transfer_agreed)


def record(db: Session, user: User, llm_transfer_agreed: bool) -> Consent:
    c = Consent(user_id=user.id, version=CONSENT_VERSION, llm_transfer_agreed=llm_transfer_agreed)
    db.add(c)
    db.flush()
    return c
