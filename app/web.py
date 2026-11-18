"""Web plumbing shared by every router: templates, flash messages, CSRF, auth dependencies.

HOW ROUTERS USE THIS (the contract the rest of the app follows)
  GET  handler:  db: Session = Depends(get_db),  user: User = Depends(member_r)
  POST handler:  db: Session = Depends(get_wdb), user: User = Depends(member_w), _=Depends(csrf_protect)
`get_db` / `get_wdb` and the user dependencies share ONE session per request, so objects loaded by the
user dependency can be modified and are committed together with everything else the handler writes.

  member_*  logged in + onboarded + has access (trial / subscription / free pilot)   -> learning & coaching pages
  user_*    logged in only                                                           -> settings, privacy, billing, pause
  staff_*   role admin or teacher        admin_*  role admin only
  optional_user_r   anonymous allowed (landing page etc.); sets request.state.user if logged in
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from . import i18n
from .config import LOCALES, get_settings
from .db import get_db, get_wdb
from .models import User
from .security import (CSRF_COOKIE, FLASH_COOKIE, LANG_COOKIE, SESSION_COOKIE, SESSION_MAX_AGE, csrf_ok, make_session_token,
                       new_csrf_token, read_session_token)
from .services import billing

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class NeedLogin(Exception):
    def __init__(self, next_url: str = "/today"):
        self.next_url = next_url


class NeedOnboarding(Exception):
    pass


class NeedAccess(Exception):
    pass


# ----------------------------------------------------------------------------- flash
def _flash_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret, salt="flash")


def redirect(url: str, flash: str | None = None, level: str = "info", status: int = 303, **params: Any) -> RedirectResponse:
    """303 redirect (POST -> GET). `flash` is an i18n key shown once on the next page; params fill {placeholders}."""
    resp = RedirectResponse(url, status_code=status)
    if flash:
        resp.set_cookie(FLASH_COOKIE, _flash_serializer().dumps({"k": flash, "l": level, "p": params}), max_age=60,
                        httponly=True, samesite="lax", secure=get_settings().cookie_secure, path="/")
    return resp


def _read_flash(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        data = _flash_serializer().loads(raw)
        return {"key": str(data["k"]), "level": str(data.get("l", "info")), "params": dict(data.get("p") or {})}
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- rendering
def render(request: Request, template: str, context: dict[str, Any] | None = None, status: int = 200,
           user: User | None = None):
    """Render a template with the standard context: t(), locale, user, csrf_token, flash, languages, app."""
    user = user or getattr(request.state, "user", None)
    locale = i18n.pick_locale(request, user.locale if user else None)
    cookie_token = request.cookies.get(CSRF_COOKIE)
    token = cookie_token if cookie_token and csrf_ok(cookie_token, cookie_token) else new_csrf_token()
    settings = get_settings()
    flash = _read_flash(request)
    ctx: dict[str, Any] = {
        "request": request,
        "user": user,
        "locale": locale,
        "t": lambda key, **kw: i18n.t(locale, key, **kw),
        "csrf_token": token,
        "languages": [(code, i18n.LANGUAGE_NAMES[code]) for code in LOCALES],
        "flash": {"level": flash["level"], "text": i18n.t(locale, flash["key"], **flash["params"])} if flash else None,
        "app": {"name": "華語教練 Huayu Coach", "billing_enabled": settings.billing_enabled,
                "support_email": settings.support_email, "env": settings.env},
        "path": request.url.path,
        **(context or {}),
    }
    response = templates.TemplateResponse(request, template, ctx, status_code=status)
    if token != cookie_token:
        response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
    if flash:
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response


# ----------------------------------------------------------------------------- csrf
async def csrf_protect(request: Request) -> None:
    """Dependency for every state-changing request. Form field `csrf` or header `X-CSRF-Token`."""
    submitted = request.headers.get("x-csrf-token")
    if submitted is None and "form" in request.headers.get("content-type", ""):
        submitted = (await request.form()).get("csrf")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), submitted if isinstance(submitted, str) else None):
        raise HTTPException(status_code=403, detail="csrf")


# ----------------------------------------------------------------------------- auth
def login_response(response, user: User) -> None:
    response.set_cookie(SESSION_COOKIE, make_session_token(user.id, user.session_version, user.public_id), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax", secure=get_settings().cookie_secure, path="/")


def logout_response(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def set_language_cookie(response, code: str) -> None:
    if code in LOCALES:
        response.set_cookie(LANG_COOKIE, code, max_age=365 * 24 * 3600, samesite="lax", secure=get_settings().cookie_secure, path="/")


def _load(request: Request, db: Session) -> User | None:
    parsed = read_session_token(request.cookies.get(SESSION_COOKIE))
    if not parsed:
        return None
    user = db.get(User, parsed[0])
    if user is None or user.session_version != parsed[1] or user.public_id != parsed[2]:
        return None
    request.state.user = user
    return user


def _need_login(request: Request) -> NeedLogin:
    url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return NeedLogin(url if request.method == "GET" else "/today")


def optional_user_r(request: Request, db: Session = Depends(get_db)) -> User | None:
    return _load(request, db)


def user_r(request: Request, db: Session = Depends(get_db)) -> User:
    if (user := _load(request, db)) is None:
        raise _need_login(request)
    return user


def user_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    if (user := _load(request, db)) is None:
        raise _need_login(request)
    return user


def _member(request: Request, db: Session, user: User | None) -> User:
    if user is None:
        raise _need_login(request)
    if user.role == "learner" and user.onboarded_at is None:
        raise NeedOnboarding()
    if not billing.access(db, user).allowed:
        raise NeedAccess()
    return user


def member_r(request: Request, db: Session = Depends(get_db)) -> User:
    return _member(request, db, _load(request, db))


def member_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    return _member(request, db, _load(request, db))


def _role(request: Request, db: Session, user: User | None, roles: tuple[str, ...]) -> User:
    if user is None:
        raise _need_login(request)
    if user.role not in roles:
        raise HTTPException(status_code=403, detail="forbidden")
    return user


def staff_r(request: Request, db: Session = Depends(get_db)) -> User:
    return _role(request, db, _load(request, db), ("admin", "teacher"))


def staff_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    return _role(request, db, _load(request, db), ("admin", "teacher"))


def admin_r(request: Request, db: Session = Depends(get_db)) -> User:
    return _role(request, db, _load(request, db), ("admin",))


def admin_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    return _role(request, db, _load(request, db), ("admin",))
