"""Erasing a person. One implementation for the learner's own "delete my account" button and the operator's
`python -m app.cli delete-user` (for requests that arrive by e-mail)."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AuditLog, Payment, User
from . import mailer

DELETED_MARKER = "(deleted account)"


def erase_account(db: Session, user: User) -> None:
    """Delete the user and everything learner-owned (the database cascades), and scrub what is deliberately kept:
    * audit-log rows about this person lose their staff-written free-text note and their anonymous code;
    * payment rows are kept for bookkeeping, detached from the person and without their note;
    * any e-mail the app wrote to data/outbox for this address is removed.
    One audit row records only that an account was deleted."""
    for row in db.scalars(select(AuditLog).where(AuditLog.target == user.public_id)):
        row.detail = {k: v for k, v in (row.detail or {}).items() if k != "note"}
        row.target = DELETED_MARKER
    db.add(AuditLog(actor_id=None, action="account_deleted", target=DELETED_MARKER, detail={"role": user.role}))
    db.execute(update(Payment).where(Payment.user_id == user.id).values(note=None))
    email = user.email
    db.delete(user)
    db.flush()
    mailer.purge_outbox(email)


def is_last_admin(db: Session, user: User) -> bool:
    from sqlalchemy import func
    return user.role == "admin" and (db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0) <= 1
