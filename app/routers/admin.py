"""Staff area: dashboard, question bank, users, plans/payments, audit log.

Roles: teachers (staff_*) may only review and edit questions; admins (admin_*) can do everything.
Every admin POST needs a CSRF token and leaves an AuditLog row (the billing service writes its own rows for grant/activate).
Never shown here: password hashes, learners' free-text self reports or comments, anything that would allow impersonation.

The question-bank rules (validation, import, export, next id) are plain functions in the first half of this module so they
can be unit-tested without HTTP; they could move to services/ unchanged.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import Any, Iterator, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request
from fastapi import UploadFile as FormUpload
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..config import get_settings
from ..db import get_db, get_wdb
from ..models import AuditLog, BillingPlan, Interruption, Payment, Question, Subscription, User
from ..services import admin_stats, billing
from ..timeutil import utcnow
from .. import web as _web
from ..web import admin_r, redirect, render, staff_r

router = APIRouter()

STAGES = ("pretest", "daily", "posttest")
LEVELS = ("A1", "A2")
SKILLS = ("vocab", "grammar", "reading")
REVIEW_STATUSES = admin_stats.REVIEW_STATUSES
ROLES = ("learner", "teacher", "admin")

MAX_UPLOAD_BYTES = 1024 * 1024  # 1 MB
MAX_IMPORT_ITEMS = 500
QUESTIONS_PER_PAGE = 25
USERS_PER_PAGE = 25
AUDIT_PER_PAGE = 50
PAYMENTS_SHOWN = 50
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,12}")  # always used with fullmatch (`$` would accept a trailing newline)
PLAN_CODE_RE = re.compile(r"[a-z][a-z0-9_]{1,19}")
MAX_DB_INT = 2_147_483_647

# fields a learner reads or answers against: changing them on an approved question sends it back to review
CONTENT_FIELDS = ("question_zh", "options", "correct_index", "explanation_vi", "explanation_zh", "explanation_en",
                  "stage", "level", "skill", "topic")


class AdminError(Exception):
    """Business-rule failure with a stable code (args[0]) that maps to admin.flash.err_<code>."""

    @property
    def code(self) -> str:
        return str(self.args[0])


# ============================================================================ error pages after a write session
# Foundation quirk: when a handler or dependency raises HTTPException (403 / 404 / CSRF failure) inside a write request,
# get_wdb rolls the session back, which expires the logged-in user; the error page then fails reading user.locale.
# Detaching the objects first keeps their loaded attributes readable. Remove when db.get_wdb does this itself.
@contextmanager
def _error_page_safe(db: Session) -> Iterator[None]:
    try:
        yield
    except HTTPException:
        db.expunge_all()
        raise


def _not_found(db: Session) -> HTTPException:
    db.expunge_all()
    return HTTPException(status_code=404)


def staff_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    with _error_page_safe(db):
        return _web.staff_w(request, db)


def admin_w(request: Request, db: Session = Depends(get_wdb)) -> User:
    with _error_page_safe(db):
        return _web.admin_w(request, db)


async def csrf_protect(request: Request, db: Session = Depends(get_wdb)) -> None:
    with _error_page_safe(db):
        await _web.csrf_protect(request)


# ============================================================================ helpers (rendering, paging, audit)
def _render(request: Request, template: str, ctx: dict[str, Any] | None = None, status: int = 200, user: User | None = None):
    tz = ZoneInfo(get_settings().timezone)

    def fmt_dt(dt: datetime | None) -> str:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else "—"

    def fmt_date(dt: datetime | None) -> str:
        return dt.astimezone(tz).strftime("%Y-%m-%d") if dt else "—"

    return render(request, template, {
        "fmt_dt": fmt_dt, "fmt_date": fmt_date,
        "pct": lambda x: f"{x * 100:.0f}%", "one": lambda x: f"{x:.1f}", "twd": lambda n: f"NT$ {n:,}",
        **(ctx or {})}, status=status, user=user)


def clean_text(value: str | None) -> str:
    """Strip control characters (keeps newlines) and surrounding spaces."""
    return "".join(ch for ch in (value or "") if ch == "\n" or ch >= " ").strip()


def _is_uint(s: str) -> bool:
    return s.isascii() and s.isdigit()


def _int(raw: Any, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(str(raw).strip()), lo), hi)
    except (TypeError, ValueError):
        return default


def _choice(raw: str | None, allowed: tuple[str, ...]) -> str:
    return raw if raw in allowed else ""


def _audit(db: Session, actor: User, action: str, target: str | None = None, detail: dict[str, Any] | None = None) -> None:
    db.add(AuditLog(actor_id=actor.id, action=action[:40], target=target[:60] if target else None, detail=detail or {}))


def _safe_next(raw: str | None, default: str) -> str:
    if raw and raw.startswith("/admin/questions") and len(raw) < 400 and "//" not in raw and "\\" not in raw \
            and all(ch >= " " for ch in raw):
        return raw
    return default


@dataclass
class Pager:
    page: int
    pages: int
    total: int
    prev_url: str | None
    next_url: str | None


def _paginate(db: Session, stmt, page: int, per_page: int, path: str, params: dict[str, str], scalars: bool = True):
    """Rows of one page + a Pager. `stmt` must have a stable ORDER BY."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, ceil(total / per_page))
    page = min(max(page, 1), pages)
    query = stmt.limit(per_page).offset((page - 1) * per_page)
    rows = list(db.scalars(query)) if scalars else list(db.execute(query).all())

    def url(n: int) -> str:
        return path + "?" + urlencode({**{k: v for k, v in params.items() if v}, "page": n})

    return rows, Pager(page, pages, total, url(page - 1) if page > 1 else None, url(page + 1) if page < pages else None)


def _user_or_404(db: Session, uid: int) -> User:
    target = db.get(User, uid) if 0 < uid <= MAX_DB_INT else None
    if target is None:
        raise _not_found(db)
    return target


# ============================================================================ question bank rules (no HTTP in here)
def _text(raw: Mapping[str, Any], key: str, errors: dict[str, str], *, required: bool = False, limit: int) -> str | None:
    value = raw.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors[key] = "invalid_type"
        return None
    value = clean_text(value)
    if not value:
        if required:
            errors[key] = "required"
        return None
    if len(value) > limit:
        errors[key] = "too_long"
        return None
    return value


def validate_question(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """(clean values, {field: error code}). Used by the edit form and by the importer, so both enforce the same rules."""
    errors: dict[str, str] = {}
    out: dict[str, Any] = {}
    out["question_zh"] = _text(raw, "question_zh", errors, required=True, limit=500)

    options = raw.get("options")
    if not isinstance(options, (list, tuple)) or len(options) != 4:
        errors["options"] = "options_count"
    elif any(not isinstance(o, str) for o in options):
        errors["options"] = "invalid_type"
    else:
        cleaned = [clean_text(o) for o in options]
        if any(not o for o in cleaned):
            errors["options"] = "options_empty"
        elif any(len(o) > 200 for o in cleaned):
            errors["options"] = "too_long"
        elif len({o.casefold() for o in cleaned}) != 4:
            errors["options"] = "options_distinct"
        else:
            out["options"] = cleaned

    idx = raw.get("correct_index")
    if isinstance(idx, str) and _is_uint(idx.strip()):
        idx = int(idx.strip())
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx <= 3:
        errors["correct_index"] = "correct_index"
    else:
        out["correct_index"] = idx

    out["topic"] = _text(raw, "topic", errors, limit=30)
    for key, allowed in (("stage", STAGES), ("level", LEVELS), ("skill", SKILLS)):
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
        else:
            errors[key] = "invalid_choice"
    for key in ("explanation_vi", "explanation_zh", "explanation_en"):
        out[key] = _text(raw, key, errors, limit=1000)
    out["review_note"] = _text(raw, "review_note", errors, limit=500)

    if "day" in raw:
        day = raw["day"]
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 365:
            errors["day"] = "invalid_number"
        else:
            out["day"] = day
    if "source" in raw:
        out["source"] = _text(raw, "source", errors, limit=60)
    if raw.get("review_status") is not None and raw.get("review_status") not in REVIEW_STATUSES:
        errors["review_status"] = "invalid_choice"
    return out, errors


def next_question_id(db: Session) -> str:
    """'Q' + the next number after the highest existing Q-number (Q050 -> Q051)."""
    highest = 0
    for qid in db.scalars(select(Question.id)):
        if (m := re.fullmatch(r"Q(\d+)", qid)):
            highest = max(highest, int(m.group(1)))
    return f"Q{highest + 1:03d}"


def export_question(q: Question) -> dict[str, Any]:
    """Same shape as seeds/questions.json (plus explanation_en)."""
    return {"id": q.id, "stage": q.stage, "day": q.day, "level": q.level, "skill": q.skill, "topic": q.topic,
            "question_zh": q.question_zh, "options": list(q.options), "correct_index": q.correct_index,
            "explanation_vi": q.explanation_vi, "explanation_zh": q.explanation_zh, "explanation_en": q.explanation_en,
            "review_status": q.review_status, "review_note": q.review_note, "source": q.source}


@dataclass
class ImportReport:
    added: int = 0
    skipped: int = 0
    invalid: list[tuple[str, dict[str, str]]] = field(default_factory=list)  # (reference, {field: code})
    fatal: str | None = None  # whole file refused: too_large | bad_json | bad_format | too_many


def import_questions(db: Session, data: bytes) -> ImportReport:
    """Insert only NEW ids, always as pending_review (an imported file is never trusted as already reviewed)."""
    report = ImportReport()
    if len(data) > MAX_UPLOAD_BYTES:
        report.fatal = "too_large"
        return report
    try:
        items = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        report.fatal = "bad_json"
        return report
    if not isinstance(items, list):
        report.fatal = "bad_format"
        return report
    if len(items) > MAX_IMPORT_ITEMS:
        report.fatal = "too_many"
        return report

    candidates: list[tuple[str, dict[str, Any]]] = []
    for pos, item in enumerate(items, start=1):
        ref = f"#{pos}"
        if not isinstance(item, dict):
            report.invalid.append((ref, {"item": "not_object"}))
            continue
        qid = item.get("id")
        if isinstance(qid, str) and ID_RE.fullmatch(qid):
            ref = qid
        clean, errors = validate_question(item)
        if ref.startswith("#"):
            errors["id"] = "bad_id"
        if errors:
            report.invalid.append((ref, errors))
        else:
            candidates.append((ref, clean))

    existing = set(db.scalars(select(Question.id).where(Question.id.in_([ref for ref, _ in candidates])))) if candidates else set()
    seen: set[str] = set()
    for ref, clean in candidates:
        if ref in existing or ref in seen:
            report.skipped += 1
            continue
        seen.add(ref)
        db.add(Question(id=ref, review_status="pending_review", reviewer_id=None, reviewed_at=None, active=True,
                        day=clean.get("day", 0), source=clean.get("source") or "import", **{
                            k: clean[k] for k in ("stage", "level", "skill", "topic", "question_zh", "options", "correct_index",
                                                  "explanation_vi", "explanation_zh", "explanation_en", "review_note")}))
        report.added += 1
    return report


def change_role(db: Session, actor: User, target: User, new_role: str) -> bool:
    """Returns True if the role changed. Refuses: unknown role, own role, demoting the last admin."""
    if new_role not in ROLES:
        raise AdminError("invalid_role")
    if target.id == actor.id:
        raise AdminError("own_role")
    if target.role == new_role:
        return False
    if target.role == "admin" and (db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0) <= 1:
        raise AdminError("last_admin")
    old, target.role = target.role, new_role
    _audit(db, actor, "role_changed", target.public_id, {"from": old, "to": new_role})
    return True


# ============================================================================ dashboard
@router.get("/admin")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(staff_r)):
    is_admin = user.role == "admin"
    ctx: dict[str, Any] = {"is_admin": is_admin}
    if is_admin:
        ctx["d"] = admin_stats.dashboard(db)
        ctx["d_question_bank"] = ctx["d"].question_bank
    else:  # teachers see the question bank only
        ctx["d_question_bank"] = admin_stats.question_bank(db)
    ctx["billing_enabled"] = get_settings().billing_enabled
    return _render(request, "admin/dashboard.html", ctx, user=user)


# ============================================================================ questions
def _banner() -> dict[str, Any]:
    return {"allow_unreviewed": bool(get_settings().allow_unreviewed_questions)}


@router.get("/admin/questions")
def question_list(request: Request, db: Session = Depends(get_db), user: User = Depends(staff_r)):
    qp = request.query_params
    f = {"stage": _choice(qp.get("stage"), STAGES), "skill": _choice(qp.get("skill"), SKILLS),
         "level": _choice(qp.get("level"), LEVELS), "status": _choice(qp.get("status"), REVIEW_STATUSES),
         "topic": clean_text(qp.get("topic"))[:30], "q": clean_text(qp.get("q"))[:100]}
    stmt = select(Question).order_by(Question.id)
    for col, key in ((Question.stage, "stage"), (Question.skill, "skill"), (Question.level, "level"),
                     (Question.review_status, "status"), (Question.topic, "topic")):
        if f[key]:
            stmt = stmt.where(col == f[key])
    if f["q"]:
        stmt = stmt.where(or_(Question.id.contains(f["q"], autoescape=True), Question.question_zh.contains(f["q"], autoescape=True),
                              Question.topic.contains(f["q"], autoescape=True)))
    rows, pager = _paginate(db, stmt, _int(qp.get("page"), 1, 1, 100000), QUESTIONS_PER_PAGE, "/admin/questions", f)
    topics = list(db.scalars(select(Question.topic).where(Question.topic.is_not(None)).distinct().order_by(Question.topic)))
    return _render(request, "admin/questions.html", {
        "questions": rows, "pager": pager, "f": f, "topics": topics, "stages": STAGES, "levels": LEVELS, "skills": SKILLS,
        "statuses": REVIEW_STATUSES, "list_url": request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        **_banner()}, user=user)


def _blank_form() -> dict[str, str]:
    return {"question_zh": "", "option_0": "", "option_1": "", "option_2": "", "option_3": "", "correct": "0", "topic": "",
            "level": "A1", "skill": "vocab", "stage": "daily", "explanation_vi": "", "explanation_zh": "", "explanation_en": "",
            "review_note": ""}


def _form_from_question(q: Question) -> dict[str, str]:
    opts = [str(o) for o in (q.options or [])] + [""] * 4
    return {"question_zh": q.question_zh, **{f"option_{i}": opts[i] for i in range(4)}, "correct": str(q.correct_index),
            "topic": q.topic or "", "level": q.level, "skill": q.skill, "stage": q.stage,
            "explanation_vi": q.explanation_vi or "", "explanation_zh": q.explanation_zh or "",
            "explanation_en": q.explanation_en or "", "review_note": q.review_note or ""}


def question_fields(question_zh: str = Form(""), option_0: str = Form(""), option_1: str = Form(""), option_2: str = Form(""),
                    option_3: str = Form(""), correct: str = Form(""), topic: str = Form(""), level: str = Form(""),
                    skill: str = Form(""), stage: str = Form(""), explanation_vi: str = Form(""),
                    explanation_zh: str = Form(""), explanation_en: str = Form(""), review_note: str = Form("")) -> dict[str, str]:
    return {"question_zh": question_zh, "option_0": option_0, "option_1": option_1, "option_2": option_2, "option_3": option_3,
            "correct": correct, "topic": topic, "level": level, "skill": skill, "stage": stage,
            "explanation_vi": explanation_vi, "explanation_zh": explanation_zh, "explanation_en": explanation_en,
            "review_note": review_note}


def _raw_from_form(f: Mapping[str, str]) -> dict[str, Any]:
    return {"question_zh": f["question_zh"], "options": [f[f"option_{i}"] for i in range(4)], "correct_index": f["correct"],
            "topic": f["topic"], "level": f["level"], "skill": f["skill"], "stage": f["stage"],
            "explanation_vi": f["explanation_vi"], "explanation_zh": f["explanation_zh"],
            "explanation_en": f["explanation_en"], "review_note": f["review_note"]}


def _question_page(request: Request, user: User, question: Question | None, values: dict[str, str],
                   errors: dict[str, str] | None = None, status: int = 200, reviewer: User | None = None):
    return _render(request, "admin/question_form.html", {
        "question": question, "v": values, "errors": errors or {}, "reviewer": reviewer, "stages": STAGES, "levels": LEVELS,
        "skills": SKILLS, **_banner()}, status=status, user=user)


@router.get("/admin/questions/new")
def question_new(request: Request, user: User = Depends(staff_r)):
    return _question_page(request, user, None, _blank_form())


@router.post("/admin/questions/new")
def question_create(request: Request, fields: dict[str, str] = Depends(question_fields), db: Session = Depends(get_wdb),
                    user: User = Depends(staff_w), _=Depends(csrf_protect)):
    clean, errors = validate_question(_raw_from_form(fields))
    if errors:
        return _question_page(request, user, None, fields, errors, status=422)
    qid = next_question_id(db)
    db.add(Question(id=qid, review_status="pending_review", source="admin", active=True, day=0, **clean))
    _audit(db, user, "question_created", qid, {"stage": clean["stage"], "level": clean["level"], "skill": clean["skill"]})
    db.commit()
    return redirect(f"/admin/questions/{qid}", flash="admin.flash.question_created", level="success", id=qid)


@router.get("/admin/questions/export")
def question_export(db: Session = Depends(get_db), user: User = Depends(staff_r)):
    data = [export_question(q) for q in db.scalars(select(Question).order_by(Question.id))]
    body = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return Response(body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="questions-export.json"'})


async def _upload_size_guard(request: Request) -> None:
    """Refuse an oversized upload before it is parsed (the importer re-checks the real size)."""
    declared = request.headers.get("content-length")
    if declared and _is_uint(declared) and int(declared) > MAX_UPLOAD_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="too_large")


@router.post("/admin/questions/import")
async def question_import(request: Request, file: FormUpload | None = File(None), csrf: str = Form(""),
                          db: Session = Depends(get_wdb), user: User = Depends(staff_w),
                          _size=Depends(_upload_size_guard), _csrf=Depends(csrf_protect)):
    # `file` and `csrf` are declared so the whole body is read before staff_w opens the SQLite write transaction:
    # a stalled upload must not hold the single writer lock.
    upload = file
    if not isinstance(upload, UploadFile):
        return redirect("/admin/questions", flash="admin.flash.err_no_file", level="error")
    data = await upload.read(MAX_UPLOAD_BYTES + 1)

    def work() -> ImportReport:
        rep = import_questions(db, data)
        detail: dict[str, Any] = {"added": rep.added, "skipped": rep.skipped, "invalid": len(rep.invalid)}
        if rep.fatal:
            detail["error"] = rep.fatal
        _audit(db, user, "questions_imported", None, detail)
        db.commit()
        return rep

    report = await run_in_threadpool(work)
    return _render(request, "admin/import_result.html", {"report": report, "shown_invalid": report.invalid[:50]},
                   status=422 if report.fatal else 200, user=user)


@router.get("/admin/questions/{qid}")
def question_edit(qid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(staff_r)):
    q = db.get(Question, qid)
    if q is None:
        raise _not_found(db)
    reviewer = db.get(User, q.reviewer_id) if q.reviewer_id else None
    return _question_page(request, user, q, _form_from_question(q), reviewer=reviewer)


@router.post("/admin/questions/{qid}/review")
def question_review(qid: str, status: str = Form(""), next: str = Form(""), db: Session = Depends(get_wdb),
                    user: User = Depends(staff_w), _=Depends(csrf_protect)):
    q = db.get(Question, qid)
    if q is None:
        raise _not_found(db)
    back = _safe_next(next, f"/admin/questions/{qid}")
    if status not in REVIEW_STATUSES:
        return redirect(back, flash="admin.flash.err_invalid_status", level="error")
    old = q.review_status
    q.review_status, q.reviewer_id, q.reviewed_at = status, user.id, utcnow()
    _audit(db, user, "question_review", qid, {"from": old, "to": status})
    db.commit()
    return redirect(back, flash="admin.flash.review_saved", level="success", id=qid)


@router.post("/admin/questions/{qid}")
def question_save(qid: str, request: Request, fields: dict[str, str] = Depends(question_fields), db: Session = Depends(get_wdb),
                  user: User = Depends(staff_w), _=Depends(csrf_protect)):
    q = db.get(Question, qid)
    if q is None:
        raise _not_found(db)
    clean, errors = validate_question(_raw_from_form(fields))
    if errors:
        return _question_page(request, user, q, fields, errors, status=422,
                              reviewer=db.get(User, q.reviewer_id) if q.reviewer_id else None)
    content_changed = any((getattr(q, k) or None) != (clean[k] or None) for k in CONTENT_FIELDS)
    changed = [k for k, v in clean.items() if (getattr(q, k) or None) != (v or None)]
    for key, value in clean.items():
        setattr(q, key, value)
    back_to_review = content_changed and q.review_status == "approved"
    if back_to_review:
        q.review_status, q.reviewer_id, q.reviewed_at = "pending_review", None, None
    _audit(db, user, "question_edited", qid, {"fields": changed, "back_to_review": back_to_review})
    db.commit()
    return redirect(f"/admin/questions/{qid}", level="success",
                    flash="admin.flash.question_saved_review" if back_to_review else "admin.flash.question_saved")


# ============================================================================ users (admin only)
@router.get("/admin/users")
def user_list(request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_r)):
    qp = request.query_params
    f = {"q": clean_text(qp.get("q"))[:100], "role": _choice(qp.get("role"), ROLES)}
    stmt = select(User).order_by(User.id.desc())
    if f["q"]:
        stmt = stmt.where(or_(User.public_id.contains(f["q"], autoescape=True), User.email.contains(f["q"], autoescape=True)))
    if f["role"]:
        stmt = stmt.where(User.role == f["role"])
    rows, pager = _paginate(db, stmt, _int(qp.get("page"), 1, 1, 100000), USERS_PER_PAGE, "/admin/users", f)
    return _render(request, "admin/users.html", {
        "rows": [(u, billing.access(db, u)) for u in rows], "pager": pager, "f": f, "roles": ROLES}, user=admin)


@router.get("/admin/users/{uid:int}")
def user_detail(uid: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_r)):
    target = _user_or_404(db, uid)
    subs = list(db.scalars(select(Subscription).where(Subscription.user_id == uid).order_by(Subscription.id.desc())))
    payments = list(db.scalars(select(Payment).where(Payment.user_id == uid).order_by(Payment.id.desc())))
    # reason + status only: the learner's own words (self_report) are deliberately never selected
    interruptions = db.execute(select(Interruption.reason_code, Interruption.status, Interruption.created_at)
                               .where(Interruption.user_id == uid).order_by(Interruption.id.desc()).limit(10)).all()
    plans = list(db.scalars(select(BillingPlan).where(BillingPlan.active.is_(True)).order_by(BillingPlan.price_twd)))
    return _render(request, "admin/user.html", {
        "target": target, "plan": target.plan, "access": billing.access(db, target), "subs": subs, "payments": payments,
        "interruptions": interruptions, "plans": plans, "roles": ROLES, "is_self": target.id == admin.id,
        "billing_enabled": get_settings().billing_enabled}, user=admin)


def _back_to_user(uid: int, flash: str, level: str = "success", **params: Any):
    return redirect(f"/admin/users/{uid}", flash=flash, level=level, **params)


@router.post("/admin/users/{uid:int}/grant")
def user_grant(uid: int, days: str = Form(""), note: str = Form(""), db: Session = Depends(get_wdb),
               admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    target = _user_or_404(db, uid)
    n = _int(days, 0, 0, 10_000)
    if not 1 <= n <= 365:
        return _back_to_user(uid, "admin.flash.err_invalid_days", "error")
    if target.role != "learner":
        return _back_to_user(uid, "admin.flash.err_not_learner", "error")
    if billing.recent_duplicate_grant(db, target, n):
        return _back_to_user(uid, "admin.flash.err_duplicate", "error")
    billing.grant_days(db, target, n, actor=admin, note=clean_text(note)[:200] or None)  # the service writes the audit row
    db.commit()
    return _back_to_user(uid, "admin.flash.granted", days=n)


@router.post("/admin/users/{uid:int}/activate")
def user_activate(uid: int, plan_code: str = Form(""), amount: str = Form(""), note: str = Form(""),
                  db: Session = Depends(get_wdb), admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    target = _user_or_404(db, uid)
    if target.role != "learner":
        return _back_to_user(uid, "admin.flash.err_not_learner", "error")
    amount_twd: int | None = None
    if amount.strip():
        if not _is_uint(amount.strip()) or int(amount.strip()) > 1_000_000:
            return _back_to_user(uid, "admin.flash.err_invalid_amount", "error")
        amount_twd = int(amount.strip())
    if billing.recent_duplicate_payment(db, target, plan_code.strip(), amount_twd):
        return _back_to_user(uid, "admin.flash.err_duplicate", "error")  # a double click must not bill the learner twice
    try:
        sub = billing.activate(db, target, plan_code.strip(), actor=admin, note=clean_text(note)[:300] or None, amount_twd=amount_twd)
    except ValueError:
        return _back_to_user(uid, "admin.flash.err_unknown_plan", "error")
    db.commit()
    return _back_to_user(uid, "admin.flash.activated", until=sub.current_period_end.astimezone(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%d"))


@router.post("/admin/users/{uid:int}/reset-placement")
def user_reset_placement(uid: int, db: Session = Depends(get_wdb), admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    target = _user_or_404(db, uid)
    if target.role != "learner":
        return _back_to_user(uid, "admin.flash.err_not_learner", "error")
    target.pretest_done_at, target.level = None, None
    _audit(db, admin, "placement_reset", target.public_id, {})
    db.commit()
    return _back_to_user(uid, "admin.flash.placement_reset")


@router.post("/admin/users/{uid:int}/role")
def user_role(uid: int, role: str = Form(""), db: Session = Depends(get_wdb), admin: User = Depends(admin_w),
              _=Depends(csrf_protect)):
    target = _user_or_404(db, uid)
    try:
        change_role(db, admin, target, role.strip())
    except AdminError as exc:
        return _back_to_user(uid, f"admin.flash.err_{exc.code}", "error")
    db.commit()
    return _back_to_user(uid, "admin.flash.role_saved")


@router.post("/admin/users/{uid:int}/sessions")
def user_end_sessions(uid: int, db: Session = Depends(get_wdb), admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    target = _user_or_404(db, uid)
    target.session_version += 1
    _audit(db, admin, "sessions_ended", target.public_id)
    db.commit()
    return _back_to_user(uid, "admin.flash.sessions_ended")


# ============================================================================ plans, payments, audit (admin only)
def _plan_fields(name_vi: str, name_zh: str, name_en: str, price_twd: str, period_days: str) -> dict[str, Any] | None:
    names = {"vi": clean_text(name_vi), "zh-TW": clean_text(name_zh), "en": clean_text(name_en)}
    if any(not n or len(n) > 60 for n in names.values()):
        return None
    price, days = price_twd.strip(), period_days.strip()
    if not (_is_uint(price) and _is_uint(days)) or int(price) > 1_000_000 or not 1 <= int(days) <= 3650:
        return None
    return {"name": names, "price_twd": int(price), "period_days": int(days)}


@router.get("/admin/billing")
def billing_admin(request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_r)):
    plans = list(db.scalars(select(BillingPlan).order_by(BillingPlan.price_twd, BillingPlan.code)))
    payments = db.execute(select(Payment, User.public_id).outerjoin(User, User.id == Payment.user_id)
                          .order_by(Payment.id.desc()).limit(PAYMENTS_SHOWN)).all()
    return _render(request, "admin/billing.html", {
        "plans": plans, "payments": payments, "months": admin_stats.monthly_revenue(db),
        "billing_enabled": get_settings().billing_enabled}, user=admin)


@router.post("/admin/billing/plans")
def plan_create(code: str = Form(""), name_vi: str = Form(""), name_zh: str = Form(""), name_en: str = Form(""),
                price_twd: str = Form(""), period_days: str = Form(""), db: Session = Depends(get_wdb),
                admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    code = code.strip()
    fields = _plan_fields(name_vi, name_zh, name_en, price_twd, period_days)
    if not PLAN_CODE_RE.fullmatch(code):
        return redirect("/admin/billing", flash="admin.flash.err_plan_code", level="error")
    if fields is None:
        return redirect("/admin/billing", flash="admin.flash.err_plan_invalid", level="error")
    if db.get(BillingPlan, code) is not None:
        return redirect("/admin/billing", flash="admin.flash.err_plan_exists", level="error")
    db.add(BillingPlan(code=code, active=True, **fields))
    _audit(db, admin, "plan_created", code, {"price_twd": fields["price_twd"], "period_days": fields["period_days"]})
    db.commit()
    return redirect("/admin/billing", flash="admin.flash.plan_saved", level="success")


@router.post("/admin/billing/plans/{code}")
def plan_save(code: str, name_vi: str = Form(""), name_zh: str = Form(""), name_en: str = Form(""),
              price_twd: str = Form(""), period_days: str = Form(""), active: str = Form(""),
              db: Session = Depends(get_wdb), admin: User = Depends(admin_w), _=Depends(csrf_protect)):
    plan = db.get(BillingPlan, code)
    if plan is None:
        raise _not_found(db)
    fields = _plan_fields(name_vi, name_zh, name_en, price_twd, period_days)
    if fields is None:
        return redirect("/admin/billing", flash="admin.flash.err_plan_invalid", level="error")
    plan.name, plan.price_twd, plan.period_days, plan.active = fields["name"], fields["price_twd"], fields["period_days"], bool(active)
    _audit(db, admin, "plan_saved", code, {"price_twd": plan.price_twd, "period_days": plan.period_days, "active": plan.active})
    db.commit()
    return redirect("/admin/billing", flash="admin.flash.plan_saved", level="success")


def _detail_pairs(detail: Any) -> list[tuple[str, str]]:
    if not isinstance(detail, dict):
        return []
    return [(str(k)[:40], str(v)[:160]) for k, v in detail.items()]


@router.get("/admin/audit")
def audit_log(request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_r)):
    stmt = select(AuditLog, User.public_id).outerjoin(User, User.id == AuditLog.actor_id).order_by(AuditLog.id.desc())
    rows, pager = _paginate(db, stmt, _int(request.query_params.get("page"), 1, 1, 100000), AUDIT_PER_PAGE, "/admin/audit", {},
                            scalars=False)
    return _render(request, "admin/audit.html", {
        "rows": [(entry, actor, _detail_pairs(entry.detail)) for entry, actor in rows], "pager": pager}, user=admin)
