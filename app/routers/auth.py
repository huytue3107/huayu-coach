"""Accounts: register, log in/out, forgotten password.

Also home of the small helpers the account router reuses (e-mail and password rules, safe `next` redirects).
Nothing here logs e-mail addresses or passwords.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import i18n
from ..config import get_settings
from ..db import get_db, get_wdb, write_session
from ..models import PasswordReset, User
from ..security import (RateLimited, hash_password, hash_token, limiter, needs_rehash, new_token, rate_limit, verify_password)
from ..services import billing, mailer
from ..timeutil import utcnow
from ..web import csrf_protect, login_response, logout_response, optional_user_r, redirect, render

log = logging.getLogger("huayu.auth")
router = APIRouter()

RESET_MINUTES = 60
MAX_PASSWORD = 128

# A deliberately small deny-list: the passwords that are tried first in every guessing attack.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password12", "password123", "passw0rd", "p@ssw0rd", "p@ssword", "changeme", "letmein1",
    "12345678", "123456789", "1234567890", "12341234", "123123123", "1q2w3e4r", "q1w2e3r4", "zaq12wsx", "11111111",
    "00000000", "88888888", "66666666", "987654321", "12345678a", "a1234567", "a12345678", "abc12345", "abcd1234",
    "1234abcd", "qwerty123", "qwertyui", "qwertyuiop", "iloveyou", "iloveyou1", "welcome1", "admin123", "administrator",
    "monkey123", "dragon123", "football", "baseball", "sunshine", "princess", "trustno1", "taiwan123", "vietnam123",
    "huayu123", "huayu1234", "huayucoach", "aaaaaaaa", "5201314520",
})

_EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")


# ----------------------------------------------------------------------------- helpers (also used by routers/account.py)
def clean_email(raw: str) -> str | None:
    """Lower-cased address, or None when it is not a plausible e-mail (ASCII, one @, a dotted domain, max 254)."""
    email = (raw or "").strip().lower()
    return email if len(email) <= 254 and _EMAIL_RE.fullmatch(email) else None


def password_error(password: str, email: str) -> str | None:
    """i18n key of the first rule the password breaks, or None when it is acceptable."""
    if len(password) < get_settings().min_password_length:
        return "auth.err.password_short"
    if len(password) > MAX_PASSWORD:
        return "auth.err.password_long"
    low = password.lower()
    if low == email.lower():
        return "auth.err.password_same_as_email"
    if low in COMMON_PASSWORDS or len(set(low)) == 1:
        return "auth.err.password_common"
    return None


def template_limits() -> dict[str, int]:
    """Values for the {placeholders} of the error messages."""
    return {"min": get_settings().min_password_length, "max_password": MAX_PASSWORD}


def safe_next(raw: str | None) -> str | None:
    """A local path (single leading '/') or None. Never a scheme, host, '//', backslash or control character."""
    if not raw or len(raw) > 300 or not raw.startswith("/") or raw.startswith("//"):
        return None
    if "\\" in raw or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None
    parts = urlsplit(raw)
    return None if parts.scheme or parts.netloc else raw


def landing(user: User, next_url: str | None = None) -> str:
    """Where a person goes right after logging in."""
    if user.role in ("admin", "teacher"):
        return next_url or "/admin"
    if user.onboarded_at is None:
        return "/onboarding"
    return next_url or "/today"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _page(request: Request, template: str, ctx: dict | None = None, status: int = 200):
    return render(request, template, {"errors": {}, "limits": template_limits(), **(ctx or {})}, status=status)


# ----------------------------------------------------------------------------- register
def _register_page(request: Request, *, email: str = "", invite: str = "", errors: dict | None = None, status: int = 200):
    s = get_settings()
    return _page(request, "auth/register.html", {
        "form": {"email": email, "invite": invite}, "errors": errors or {}, "closed": not s.registration_open,
        "invite_required": bool(s.invite_code), "trial_days": s.trial_days if s.billing_enabled else 0}, status)


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(optional_user_r)):
    if user:
        return redirect(landing(user))
    return _register_page(request)


@router.post("/register")
def register_submit(request: Request, email: str = Form(""), password: str = Form(""), invite: str = Form(""),
                    db: Session = Depends(get_wdb), _=Depends(csrf_protect)):
    s = get_settings()
    rate_limit(request, "register", 60, 3600)  # a class registering together shares one campus IP
    if not s.registration_open:
        return _register_page(request, status=403)

    addr = clean_email(email)
    errors: dict[str, str] = {}
    if s.invite_code and not hmac.compare_digest(invite.strip().encode(), s.invite_code.encode()):
        errors["invite"] = "auth.err.invite_invalid"
    if addr is None:
        errors["email"] = "auth.err.email_invalid"
    if (problem := password_error(password, addr or email.strip().lower())):
        errors["password"] = problem
    # Hash BEFORE the first query: that query opens the write transaction (SQLite's single writer lock), and the lock
    # should not be held for the ~50 ms argon2 takes.
    hashed = None if errors else hash_password(password)
    if not errors and db.scalar(select(User.id).where(User.email == addr)) is not None:
        errors["email"] = "auth.err.email_taken"  # only revealed to people who hold a valid invite
    if errors:
        return _register_page(request, email=email.strip()[:254], invite=invite.strip()[:100], errors=errors, status=400)

    user = User(email=addr, password_hash=hashed, locale=i18n.pick_locale(request, None))
    db.add(user)
    db.flush()
    billing.start_trial(db, user)
    resp = redirect("/onboarding", flash="auth.flash.welcome", level="success")
    login_response(resp, user)
    return resp


# ----------------------------------------------------------------------------- login / logout
def _login_page(request: Request, *, email: str = "", next_url: str | None = None, error: str | None = None, status: int = 200):
    return _page(request, "auth/login.html", {"form": {"email": email}, "next": next_url or "",
                                               "errors": {"form": error} if error else {}}, status)


@router.get("/login")
def login_page(request: Request, next: str = "", user: User | None = Depends(optional_user_r)):
    target = safe_next(next)
    if user:
        return redirect(landing(user, target))
    return _login_page(request, next_url=target)


@router.post("/login")
def login_submit(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form(""),
                 db: Session = Depends(get_db), _=Depends(csrf_protect)):
    # A read session on purpose: the argon2 check of a wrong password must not hold SQLite's single writer lock, or a
    # flood of failed logins would stall every learner's writes. Only a successful login writes (below).
    addr = clean_email(email)
    rate_limit(request, "login", 240, 600)
    acct = _digest(addr or email.strip().lower()[:100])
    rate_limit(request, "login-email", 8, 600, extra_key=acct)
    limiter.check(f"login-acct:{acct}", 25, 900)  # per account, whatever addresses the guesses come from (IPv6 rotation, botnets)
    user = db.scalar(select(User).where(User.email == addr)) if addr else None
    # verify_password(…, None) burns the same time as a real check, so unknown e-mails cost the same as wrong passwords.
    ok = verify_password(password, user.password_hash if user else None) and len(password) <= MAX_PASSWORD
    if not ok or user is None:
        return _login_page(request, email=email.strip()[:254], next_url=safe_next(next), error="auth.err.login_failed", status=400)
    upgraded = hash_password(password) if needs_rehash(user.password_hash) else None
    with write_session() as wdb:
        row = wdb.get(User, user.id)
        row.last_login_at = utcnow()
        if upgraded and row.password_hash == user.password_hash:  # not if the password was changed in the meantime
            row.password_hash = upgraded
    resp = redirect(landing(user, safe_next(next)))
    login_response(resp, user)
    return resp


@router.post("/logout")
def logout(_=Depends(csrf_protect)):
    resp = redirect("/", flash="auth.flash.logged_out")
    logout_response(resp)
    return resp


# ----------------------------------------------------------------------------- forgotten password
def _send_reset_mail(to: str, locale: str, link: str) -> None:
    try:
        mailer.send_mail(to, i18n.t(locale, "auth.mail.reset_subject"),
                         i18n.t(locale, "auth.mail.reset_body", link=link, minutes=RESET_MINUTES))
    except Exception as exc:  # noqa: BLE001 - runs after the response; must not reveal anything or log the address
        log.error("password reset mail failed (%s)", type(exc).__name__)


@router.get("/forgot")
def forgot_page(request: Request):
    return _page(request, "auth/forgot.html", {"form": {"email": ""}, "minutes": RESET_MINUTES})


@router.post("/forgot")
def forgot_submit(request: Request, background: BackgroundTasks, email: str = Form(""),
                  db: Session = Depends(get_wdb), _=Depends(csrf_protect)):
    rate_limit(request, "forgot", 10, 3600)
    addr = clean_email(email)
    if addr is None:  # says nothing about whether an account exists
        return _page(request, "auth/forgot.html", {"form": {"email": email.strip()[:254]}, "minutes": RESET_MINUTES,
                                                    "errors": {"email": "auth.err.email_invalid"}}, 400)
    try:
        limiter.check(f"forgot-email:{_digest(addr)}", 3, 3600)  # protects the owner's inbox from floods
    except RateLimited:
        return redirect("/login", flash="auth.flash.reset_sent")  # same answer: a stranger cannot tell, or block, anything
    user = db.scalar(select(User).where(User.email == addr))
    if user is not None:
        now = utcnow()
        raw, hashed = new_token()
        live = db.scalars(select(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None),
                                                      PasswordReset.expires_at > now).order_by(PasswordReset.id.desc())).all()
        for old in live[2:]:  # keep the 2 newest unused links plus the new one: a stranger's request cannot invalidate yours
            old.used_at = now
        db.add(PasswordReset(user_id=user.id, token_hash=hashed, expires_at=now + timedelta(minutes=RESET_MINUTES)))
        background.add_task(_send_reset_mail, user.email, user.locale, f"{get_settings().base_url.rstrip('/')}/reset/{raw}")
    return redirect("/login", flash="auth.flash.reset_sent")


def _valid_reset(db: Session, token: str) -> PasswordReset | None:
    if not token or len(token) > 200:
        return None
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)))
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return None
    return row


@router.get("/reset/{token}")
def reset_page(token: str, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "reset", 20, 3600)
    if _valid_reset(db, token) is None:
        return _page(request, "auth/reset.html", {"valid": False}, 400)
    return _page(request, "auth/reset.html", {"valid": True, "token": token})


@router.post("/reset/{token}")
def reset_submit(token: str, request: Request, password: str = Form(""), password2: str = Form(""),
                 db: Session = Depends(get_wdb), _=Depends(csrf_protect)):
    rate_limit(request, "reset", 20, 3600)
    row = _valid_reset(db, token)
    if row is None:
        return _page(request, "auth/reset.html", {"valid": False}, 400)
    user = db.get(User, row.user_id)
    errors: dict[str, str] = {}
    if (problem := password_error(password, user.email)):
        errors["password"] = problem
    elif password != password2:
        errors["password2"] = "auth.err.password_mismatch"
    if errors:
        return _page(request, "auth/reset.html", {"valid": True, "token": token, "errors": errors}, 400)
    user.password_hash = hash_password(password)
    user.session_version += 1  # every device that was logged in is logged out
    db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
               .values(used_at=utcnow()))
    return redirect("/login", flash="auth.flash.password_reset", level="success")
