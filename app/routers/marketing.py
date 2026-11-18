"""Public website: landing page, pricing, privacy policy, terms, PWA manifest and robots.txt.

Everything here is readable without logging in (`optional_user_r`) and changes no state. A logged-in visitor on `/` is sent on
to their own home. Pages state only what the code really does: the privacy page lists the tables in app/models.py, the
pricing page reads the active BillingPlan rows, and the AI / question-review statements follow the live settings.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n
from ..config import get_settings
from ..db import get_db
from ..models import BillingPlan, User
from ..web import optional_user_r, redirect, render

router = APIRouter()

THEME_COLOR = "#14233c"
BACKGROUND_COLOR = "#f6f9fb"
# Pages a search engine has no business indexing (they need a login or hold personal data).
ROBOTS_DISALLOW = ("/admin", "/settings", "/today", "/task", "/placement", "/posttest", "/stuck", "/proposal", "/plan",
                   "/progress", "/billing")
PUBLIC_CACHE = {"Cache-Control": "public, max-age=3600"}


def _canonical(request: Request) -> str:
    return get_settings().base_url.rstrip("/") + request.url.path


def _locale(request: Request, user: User | None) -> str:
    return i18n.pick_locale(request, user.locale if user else None)


def _plan_name(plan: BillingPlan, locale: str) -> str:
    names = plan.name if isinstance(plan.name, dict) else {}
    return str(names.get(locale) or names.get("en") or plan.code)


# ----------------------------------------------------------------------------- pages
@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def landing(request: Request, user: User | None = Depends(optional_user_r)):
    if user is not None:
        return redirect("/admin" if user.role in ("admin", "teacher") else "/today")
    s = get_settings()
    return render(request, "marketing/home.html", {
        "canonical": _canonical(request),
        "billing_enabled": s.billing_enabled,
        "trial_days": s.trial_days,
        "unreviewed_served": bool(s.allow_unreviewed_questions),
    })


@router.get("/pricing", include_in_schema=False)
def pricing(request: Request, db: Session = Depends(get_db), user: User | None = Depends(optional_user_r)):
    s = get_settings()
    locale = _locale(request, user)
    rows = db.scalars(select(BillingPlan).where(BillingPlan.active.is_(True))
                      .order_by(BillingPlan.price_twd, BillingPlan.period_days, BillingPlan.code)).all()
    plans = [{"code": p.code, "name": _plan_name(p, locale), "price": f"{p.price_twd:,}", "days": p.period_days} for p in rows]
    return render(request, "marketing/pricing.html", {
        "canonical": _canonical(request), "plans": plans,
        "billing_enabled": s.billing_enabled, "trial_days": s.trial_days,
    }, user=user)


@router.get("/privacy", include_in_schema=False)
def privacy(request: Request, user: User | None = Depends(optional_user_r)):
    s = get_settings()
    return render(request, "marketing/privacy.html", {
        "canonical": _canonical(request), "llm_enabled": s.llm_provider != "none",
    }, user=user)


@router.get("/terms", include_in_schema=False)
def terms(request: Request, user: User | None = Depends(optional_user_r)):
    s = get_settings()
    return render(request, "marketing/terms.html", {
        "canonical": _canonical(request), "billing_enabled": s.billing_enabled, "trial_days": s.trial_days,
    }, user=user)


# ----------------------------------------------------------------------------- PWA + crawlers
@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    locale = get_settings().default_locale
    body: dict[str, Any] = {
        "id": "/",
        "name": "華語教練 Huayu Coach",
        "short_name": "華語教練",
        "description": i18n.t(locale, "marketing.manifest.description"),
        "lang": locale,
        "dir": "ltr",
        "start_url": "/today",
        "scope": "/",
        "display": "standalone",
        "background_color": BACKGROUND_COLOR,
        "theme_color": THEME_COLOR,
        "categories": ["education"],
        "icons": [
            {"src": "/static/manifest/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/manifest/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/manifest/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }
    return JSONResponse(body, media_type="application/manifest+json", headers=PUBLIC_CACHE)


@router.get("/robots.txt", include_in_schema=False)
def robots():
    lines = ["User-agent: *", "Allow: /", *(f"Disallow: {path}" for path in ROBOTS_DISALLOW)]
    return PlainTextResponse("\n".join(lines) + "\n", headers=PUBLIC_CACHE)
