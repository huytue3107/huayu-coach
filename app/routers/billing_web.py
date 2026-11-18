"""Learner-facing billing page. Read-only: it explains the state and how to pay, it never collects card or payment data.

It uses user_r (logged in, nothing else) on purpose: it is where a learner with an expired trial lands, so it must never
be blocked by the access check. Payments are confirmed by hand (see services.billing.activate and the admin area).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import BillingPlan, User
from ..services import billing
from ..web import render, user_r

router = APIRouter()


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or get_settings().timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(get_settings().timezone)


@router.get("/billing")
def billing_page(request: Request, db: Session = Depends(get_db), user: User = Depends(user_r)):
    settings = get_settings()
    access = billing.access(db, user)
    zone = _zone(user.timezone)

    def fmt_date(dt: datetime | None) -> str:
        return dt.astimezone(zone).strftime("%Y-%m-%d") if dt else ""

    plans = []
    if settings.billing_enabled and user.role == "learner":
        plans = list(db.scalars(select(BillingPlan).where(BillingPlan.active.is_(True))
                                .order_by(BillingPlan.price_twd, BillingPlan.code)))
    return render(request, "billing/index.html", {
        "access": access, "until": fmt_date(access.until), "plans": plans, "fmt_money": lambda n: f"NT$ {n:,}",
        "instructions": settings.payment_instructions.strip(), "support_email": settings.support_email,
        "billing_enabled": settings.billing_enabled}, user=user)
