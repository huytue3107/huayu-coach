"""Cross-cutting endpoints: health check and language switch."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db, write_session
from ..i18n import normalize
from ..models import User
from ..security import SESSION_COOKIE, read_session_token
from ..web import redirect, set_language_cookie

router = APIRouter()


@router.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)  # uptime monitors often use HEAD
def healthz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@router.get("/lang/{code}", include_in_schema=False)
def switch_language(code: str, request: Request, next: str = "/"):
    """Sets the language cookie and, for a logged-in learner, the saved preference (which otherwise wins over the cookie)."""
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = redirect(target)
    if (loc := normalize(code)):
        set_language_cookie(resp, loc)
        if (parsed := read_session_token(request.cookies.get(SESSION_COOKIE))):
            with write_session() as db:
                user = db.get(User, parsed[0])
                if user is not None and user.session_version == parsed[1] and user.public_id == parsed[2]:
                    user.locale = loc
    return resp
