"""Onboarding, settings and privacy (consent, data export, account deletion).

Every route acts on the logged-in person only: there is no user id in any URL or form, so one learner can never reach
another learner's data. These pages use user_r/user_w (not member_*) because a new learner must be able to reach
/onboarding before onboarding is finished, and anybody must be able to export or delete their data.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import func, inspect as sa_inspect, select, update
from sqlalchemy.orm import Session, selectinload

from ..config import LOCALES, get_settings
from ..db import get_db, get_wdb
from ..domain.levels import PLAN_PRESETS
from ..models import (AuditLog, Consent, Feedback, Interruption, Payment, PasswordReset, PlanChange, QuizAttempt,
                      Recommendation, StudyPlan, StudyTask, Subscription, User)
from ..security import RateLimited, hash_password, rate_limit, verify_password
from ..services import accounts, consent
from ..services.plans import create_default_plan
from ..timeutil import utcnow
from ..web import csrf_protect, login_response, logout_response, redirect, render, set_language_cookie, user_r, user_w
from .auth import MAX_PASSWORD, password_error, template_limits

router = APIRouter()

NAME_MAX = 60
DELETE_WORD = "DELETE"
DEFAULT_MINUTES = 20
# IANA name -> key of account.tz.<key>. A short allow-list on purpose (our learners live in these places).
TIMEZONES = {"Asia/Taipei": "taipei", "Asia/Ho_Chi_Minh": "hcmc", "Asia/Bangkok": "bangkok", "Asia/Tokyo": "tokyo", "UTC": "utc"}


# ----------------------------------------------------------------------------- helpers
def _limits() -> dict[str, Any]:
    return {**template_limits(), "max_name": NAME_MAX, "word": DELETE_WORD}


def clean_name(raw: str) -> str:
    """Printable characters only, whitespace collapsed ('' when nothing is left)."""
    return " ".join("".join(c for c in raw if c.isprintable() or c.isspace()).split())


def _checked(raw: str) -> bool:
    return raw.strip().lower() in ("1", "on", "true", "yes")


def _int(raw: str) -> int | None:
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _limited(request: Request, user: User, bucket: str, limit: int, window_s: int):
    """Per-learner rate limit for POST routes. Returns the finished 429 page, or None when the request may go on.

    It renders the page here, while the request's write session is still open: the global RateLimited handler would run
    after get_wdb rolled back, when `user` is expired and detached, and the error page could not read the language."""
    try:
        rate_limit(request, bucket, limit, window_s, extra_key=str(user.id))
    except RateLimited as exc:
        resp = render(request, "error.html", {"code": 429}, status=429, user=user)
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp
    return None


def _after_onboarding(user: User) -> str:
    return "/placement" if user.level is None else "/today"


# ----------------------------------------------------------------------------- onboarding
def _onboarding_page(request: Request, user: User, *, form: dict | None = None, errors: dict | None = None, status: int = 200):
    return render(request, "account/onboarding.html", {
        "form": form or {"display_name": user.display_name or "", "daily_minutes": DEFAULT_MINUTES, "consent_llm": False},
        "errors": errors or {}, "limits": _limits(), "presets": sorted(PLAN_PRESETS), "has_plan": user.plan is not None,
        "llm_enabled": get_settings().llm_provider != "none"}, status=status, user=user)


@router.get("/onboarding")
def onboarding_page(request: Request, user: User = Depends(user_r)):
    if user.role != "learner":
        return redirect("/admin")
    if user.onboarded_at is not None:
        return redirect(_after_onboarding(user))
    return _onboarding_page(request, user)


@router.post("/onboarding")
def onboarding_submit(request: Request, action: str = Form("finish"), locale: str = Form(""),
                      display_name: str = Form(""), daily_minutes: str = Form(""), consent_data: str = Form(""),
                      consent_llm: str = Form(""), _=Depends(csrf_protect), db: Session = Depends(get_wdb),
                      user: User = Depends(user_w)):
    if user.role != "learner":
        return redirect("/admin")
    if user.onboarded_at is not None:  # a double submit or an old tab: nothing more to record
        return redirect(_after_onboarding(user))

    if action == "language":  # step 1 is its own tiny form so the page can be shown in the chosen language
        resp = redirect("/onboarding")
        if locale in LOCALES:
            user.locale = locale
            set_language_cookie(resp, locale)
        return resp

    name = clean_name(display_name)
    minutes = _int(daily_minutes)
    errors: dict[str, str] = {}
    if len(name) > NAME_MAX:
        errors["display_name"] = "account.err.name_long"
    if user.plan is None and minutes not in PLAN_PRESETS:
        errors["daily_minutes"] = "account.err.minutes_invalid"
    if not _checked(consent_data):
        errors["consent_data"] = "account.err.consent_required"
    if errors:
        form = {"display_name": name[:NAME_MAX * 2], "daily_minutes": minutes, "consent_llm": _checked(consent_llm)}
        return _onboarding_page(request, user, form=form, errors=errors, status=400)

    user.display_name = name or None
    if user.plan is None:
        create_default_plan(db, user, minutes)
    consent.record(db, user, llm_transfer_agreed=_checked(consent_llm))
    user.onboarded_at = utcnow()
    return redirect(_after_onboarding(user))


# ----------------------------------------------------------------------------- settings
def _settings_page(request: Request, db: Session, user: User, *, form: dict | None = None, errors: dict | None = None,
                   status: int = 200):
    zones = [(tz, TIMEZONES.get(tz)) for tz in TIMEZONES]
    if user.timezone not in TIMEZONES:  # set elsewhere (e.g. by an admin): keep it selectable instead of silently changing it
        zones.append((user.timezone, None))
    return render(request, "account/settings.html", {
        "form": form or {"locale": user.locale, "display_name": user.display_name or "", "timezone": user.timezone},
        "errors": errors or {}, "limits": _limits(), "zones": zones,
        "llm_on": consent.llm_allowed(db, user), "llm_enabled": get_settings().llm_provider != "none"}, status=status, user=user)


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), user: User = Depends(user_r)):
    return _settings_page(request, db, user)


@router.post("/settings")
def settings_submit(request: Request, section: str = Form("profile"), locale: str = Form(""), display_name: str = Form(""),
                    timezone: str = Form(""), current_password: str = Form(""),
                    new_password: str = Form(""), new_password2: str = Form(""), _=Depends(csrf_protect),
                    db: Session = Depends(get_wdb), user: User = Depends(user_w)):
    if section == "password":
        return _change_password(request, db, user, current_password, new_password, new_password2)
    if section != "profile":
        return redirect("/settings", flash="common.flash.error", level="error")

    name = clean_name(display_name)
    errors: dict[str, str] = {}
    if locale not in LOCALES:
        errors["locale"] = "account.err.locale_invalid"
    if len(name) > NAME_MAX:
        errors["display_name"] = "account.err.name_long"
    if timezone not in TIMEZONES and timezone != user.timezone:
        errors["timezone"] = "account.err.timezone_invalid"
    if errors:
        form = {"locale": locale, "display_name": name[:NAME_MAX * 2], "timezone": timezone}
        return _settings_page(request, db, user, form=form, errors=errors, status=400)

    user.locale, user.display_name, user.timezone = locale, name or None, timezone
    resp = redirect("/settings", flash="common.flash.saved", level="success")
    set_language_cookie(resp, locale)  # keeps the language after logging out, too
    return resp


def _change_password(request: Request, db: Session, user: User, current: str, new: str, new2: str):
    if (blocked := _limited(request, user, "pwchange", 10, 3600)):
        return blocked
    errors: dict[str, str] = {}
    if len(current) > MAX_PASSWORD or not verify_password(current, user.password_hash):
        errors["current_password"] = "account.err.current_password_wrong"
    if (problem := password_error(new, user.email)):
        errors["new_password"] = problem
    elif new == current:
        errors["new_password"] = "account.err.password_same"
    elif new != new2:
        errors["new_password2"] = "auth.err.password_mismatch"
    if errors:
        return _settings_page(request, db, user, errors=errors, status=400)

    user.password_hash = hash_password(new)
    user.session_version += 1  # logs out every other device ...
    db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
               .values(used_at=utcnow()))  # ... and kills any reset link that is still waiting in an inbox
    resp = redirect("/settings", flash="account.flash.password_changed", level="success")
    login_response(resp, user)  # ... but this device keeps working
    return resp


@router.post("/settings/consent")
def settings_consent(llm_transfer_agreed: str = Form(""), _=Depends(csrf_protect), db: Session = Depends(get_wdb),
                     user: User = Depends(user_w)):
    if llm_transfer_agreed not in ("0", "1"):
        return redirect("/settings", flash="common.flash.error", level="error")
    want = llm_transfer_agreed == "1"
    if consent.llm_allowed(db, user) != want:  # append-only: a change is a new row, a repeat click adds nothing
        consent.record(db, user, llm_transfer_agreed=want)
    return redirect("/settings", flash="account.flash.consent_on" if want else "account.flash.consent_off", level="success")


# ----------------------------------------------------------------------------- export
def _value(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _row(obj: Any, drop: tuple[str, ...] = ("user_id",)) -> dict[str, Any]:
    return {a.key: _value(getattr(obj, a.key)) for a in sa_inspect(obj).mapper.column_attrs if a.key not in drop}


PROFILE_FIELDS = ("public_id", "email", "role", "locale", "timezone", "display_name", "level", "mode", "paused_until",
                  "onboarded_at", "pretest_done_at", "posttest_done_at", "created_at", "last_login_at")


def build_export(db: Session, user: User) -> dict[str, Any]:
    """Everything the app stores about this person. Never the password hash or the session counter."""
    def rows(model: Any) -> list[dict[str, Any]]:
        return [_row(r) for r in db.scalars(select(model).where(model.user_id == user.id).order_by(model.id))]

    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id))
    recs = db.scalars(select(Recommendation).where(Recommendation.user_id == user.id).options(selectinload(Recommendation.items))
                      .order_by(Recommendation.id))
    return {
        "export_version": 1,
        "exported_at": utcnow().isoformat(),
        "profile": {f: _value(getattr(user, f)) for f in PROFILE_FIELDS},
        "consents": rows(Consent),
        "plan": _row(plan) if plan else None,
        "plan_changes": rows(PlanChange),
        "quiz_attempts": rows(QuizAttempt),
        "study_tasks": rows(StudyTask),
        "interruptions": rows(Interruption),
        "recommendations": [{**_row(r), "items": [_row(i, drop=("recommendation_id",)) for i in r.items]} for r in recs],
        "feedback": rows(Feedback),
        "subscriptions": rows(Subscription),
        "payments": rows(Payment),
        # what staff recorded about this person (their free-text notes), without the staff member's identity
        "staff_actions_about_you": [_row(a, drop=("id", "actor_id")) for a in
                                    db.scalars(select(AuditLog).where(AuditLog.target == user.public_id).order_by(AuditLog.id))],
    }


@router.get("/settings/export")
def settings_export(request: Request, db: Session = Depends(get_db), user: User = Depends(user_r)):
    rate_limit(request, "export", 10, 3600, extra_key=str(user.id))
    body = json.dumps(build_export(db, user), ensure_ascii=False, indent=2, default=str)
    return Response(body, media_type="application/json; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="huayu-coach-{user.public_id}.json"', "Cache-Control": "no-store"})


# ----------------------------------------------------------------------------- delete
@router.post("/settings/delete")
def settings_delete(request: Request, password: str = Form(""), confirm: str = Form(""), _=Depends(csrf_protect),
                    db: Session = Depends(get_wdb), user: User = Depends(user_w)):
    if (blocked := _limited(request, user, "delete", 5, 3600)):
        return blocked
    errors: dict[str, str] = {}
    if len(password) > MAX_PASSWORD or not verify_password(password, user.password_hash):
        errors["password"] = "account.err.current_password_wrong"
    if confirm.strip().upper() != DELETE_WORD:
        errors["confirm"] = "account.err.delete_confirm"
    if user.role == "admin" and (db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0) <= 1:
        errors["delete"] = "account.err.last_admin"
    if errors:
        return _settings_page(request, db, user, errors=errors, status=400)

    accounts.erase_account(db, user)  # cascades every learner-owned row and scrubs what is kept (see the service)
    resp = redirect("/", flash="account.flash.deleted", level="success")
    logout_response(resp)
    return resp
