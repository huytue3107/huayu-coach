"""Application factory. Routers are discovered automatically: every module in app/routers/ that defines
`router` is mounted, so a new feature = a new file there (plus templates/ and locales/)."""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import db as dbmod
from . import routers
from .config import get_settings
from .security import RateLimited, SecurityHeadersMiddleware, apply_security_headers
from .web import NeedAccess, NeedLogin, NeedOnboarding, redirect, render

log = logging.getLogger("huayu")
STATIC_DIR = Path(__file__).parent / "static"
_SECRET_PATH = re.compile(r"(/reset/)[^\s/?\"']+")


def _redact(text: str) -> str:
    """Password-reset links carry a secret in the path: never write it to a log."""
    return _SECRET_PATH.sub(r"\1[redacted]", text)


class _RedactAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.args = tuple(_redact(a) if isinstance(a, str) else a for a in (record.args or ()))
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactAccessLog())


@asynccontextmanager
async def lifespan(app: FastAPI):
    dbmod.engine()  # fail fast on a bad database URL
    log.info("started env=%s db=%s", get_settings().env, get_settings().db_url.split("///")[0])
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Huayu Coach", lifespan=lifespan, openapi_url=None,
                  docs_url=None, redoc_url=None)  # no public API docs: the product is the website
    app.add_middleware(dbmod.WriteSessionMiddleware)  # inner: commits before the response is sent
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # HUAYU_ROUTERS=auth,account mounts only those modules (+ core). A dev/test isolation switch so one feature's tests
    # do not depend on another feature's work-in-progress; unset in production.
    only = {m.strip() for m in os.environ.get("HUAYU_ROUTERS", "").split(",") if m.strip()}
    for mod in pkgutil.iter_modules(routers.__path__):
        if only and mod.name not in only | {"core"}:
            continue
        module = importlib.import_module(f"{routers.__name__}.{mod.name}")
        if hasattr(module, "router"):
            app.include_router(module.router)

    @app.exception_handler(NeedLogin)
    async def _need_login(request: Request, exc: NeedLogin):
        from urllib.parse import quote
        return redirect(f"/login?next={quote(exc.next_url, safe='/')}")

    @app.exception_handler(NeedOnboarding)
    async def _need_onboarding(request: Request, exc: NeedOnboarding):
        return redirect("/onboarding")

    @app.exception_handler(NeedAccess)
    async def _need_access(request: Request, exc: NeedAccess):
        return redirect("/billing", flash="common.flash.access_expired", level="error")

    @app.exception_handler(RateLimited)
    async def _rate_limited(request: Request, exc: RateLimited):
        resp = render(request, "error.html", {"code": 429}, status=429)
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if exc.status_code in (403, 404, 405):
            code = 404 if exc.status_code == 405 else exc.status_code
            return render(request, "error.html", {"code": code, "csrf_failed": exc.detail == "csrf"}, status=exc.status_code)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, _redact(request.url.path))
        response = render(request, "error.html", {"code": 500}, status=500)
        apply_security_headers(response, request)  # this response is built outside the middleware stack
        return response

    return app


app = create_app()
