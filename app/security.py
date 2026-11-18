"""Security primitives: password hashing, signed session/CSRF/flash cookies, rate limiting, response headers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import get_settings

SESSION_COOKIE = "hc_session"
CSRF_COOKIE = "hc_csrf"
FLASH_COOKIE = "hc_flash"
LANG_COOKIE = "hc_lang"
SESSION_MAX_AGE = 30 * 24 * 3600

# argon2id at the OWASP-recommended minimum (19 MiB, t=2, p=1). The library default (64 MiB x 4 lanes) let ~60 concurrent
# logins allocate ~1 GB on a small VPS; the semaphore bounds the burst however many requests arrive at once.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_hash_slots = threading.BoundedSemaphore(4)
# A real hash of a random password, used to equalise timing when the e-mail does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    with _hash_slots:
        return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        with _hash_slots:
            return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _timed(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret, salt=salt)


# ----------------------------------------------------------------------------- session
def make_session_token(user_id: int, session_version: int, public_id: str) -> str:
    """The token names the person, not just a row id: ids can be reused after an account is deleted, and a stale cookie
    must never authenticate whoever registers next (the public_id is random per account)."""
    return _timed("session").dumps({"u": user_id, "v": session_version, "p": public_id})


def read_session_token(token: str | None) -> tuple[int, int, str] | None:
    if not token:
        return None
    try:
        data = _timed("session").loads(token, max_age=SESSION_MAX_AGE)
        return int(data["u"]), int(data["v"]), str(data["p"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


# ----------------------------------------------------------------------------- csrf (signed double-submit)
def new_csrf_token() -> str:
    return URLSafeSerializer(get_settings().secret, salt="csrf").dumps(secrets.token_urlsafe(16))


def csrf_ok(cookie_token: str | None, submitted: str | None) -> bool:
    if not cookie_token or not submitted or not hmac.compare_digest(cookie_token.encode(), submitted.encode()):
        return False
    try:
        URLSafeSerializer(get_settings().secret, salt="csrf").loads(cookie_token)
        return True
    except BadSignature:
        return False


# ----------------------------------------------------------------------------- one-time tokens (password reset)
def new_token() -> tuple[str, str]:
    """(token to e-mail, hash to store). Only the hash is kept in the database."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ----------------------------------------------------------------------------- rate limiting (per process)
class RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class SlidingWindow:
    """Per-process sliding-window counter. Keys that are not seen again are swept, so a flood of distinct addresses
    or e-mails cannot grow memory without bound."""

    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._hits: dict[str, tuple[deque[float], int]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str, limit: int, window_s: int) -> None:
        now = time.monotonic()
        with self._lock:
            q, _ = self._hits.get(key) or (deque(), window_s)
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                self._hits[key] = (q, window_s)
                raise RateLimited(max(1, int(q[0] + window_s - now)))
            q.append(now)
            self._hits[key] = (q, window_s)
            self._calls += 1
            if self._calls % 500 == 0 or len(self._hits) > self.MAX_KEYS:
                self._sweep(now)

    def _sweep(self, now: float) -> None:
        for k in [k for k, (q, w) in self._hits.items() if not q or q[-1] <= now - w]:
            del self._hits[k]
        if len(self._hits) > self.MAX_KEYS:  # still too many live keys: drop the least recently used half
            oldest = sorted(self._hits, key=lambda k: self._hits[k][0][-1])[: len(self._hits) - self.MAX_KEYS // 2]
            for k in oldest:
                del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()


def _bucket(raw: str) -> str:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return raw[:64]
    if ip.version == 6:  # one subscriber usually owns a whole /64: count them together
        return f"{ipaddress.ip_network(f'{ip}/64', strict=False).network_address}/64"
    return str(ip)


def client_ip(request: Request) -> str:
    if get_settings().trusted_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return _bucket(fwd.split(",")[0].strip())
    return _bucket(request.client.host) if request.client else "unknown"


def rate_limit(request: Request, bucket: str, limit: int, window_s: int, extra_key: str = "") -> None:
    """Raise RateLimited when `limit` hits per `window_s` are exceeded for this IP (+ optional extra key)."""
    limiter.check(f"{bucket}:{client_ip(request)}:{extra_key}", limit, window_s)


# ----------------------------------------------------------------------------- headers
def apply_security_headers(response, request: Request) -> None:
    h = response.headers
    h.setdefault("Content-Security-Policy",
                 "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                 "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if get_settings().cookie_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if "cache-control" not in h:
        path = request.url.path
        if path.startswith("/static") and request.method in ("GET", "HEAD"):
            # CSS/JS must be revalidated (ETag -> cheap 304) or a deploy leaves browsers on the old stylesheet
            h["Cache-Control"] = "no-cache" if path.endswith((".css", ".js")) else "public, max-age=86400"
        else:
            h["Cache-Control"] = "no-store"  # pages (and error pages) carry personal data


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        apply_security_headers(response, request)
        return response
