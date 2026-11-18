"""Learning pages: Today, placement test, daily task, post-test and the progress report.

Routers stay thin: every rule lives in services/learning.py. Every answer is a POST with a CSRF token, is followed by a
redirect (so a refresh never re-submits), and a forged id or option index ends in a friendly flash, never a 500.
POST handlers list csrf_protect FIRST: a forged request is refused before any user is loaded, which also keeps the 403
page renderable (see the change request about get_wdb in the slice report).
Note: GET /task uses the write session because it builds today's task on first visit (idempotent, serialised by the
write lock, so two simultaneous requests cannot create two tasks).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import i18n
from ..db import get_db, get_wdb
from ..domain.actions import FAR_FUTURE
from ..models import User
from ..services import coach, learning, questions
from ..services.plans import CoachError
from ..timeutil import local_today
from ..web import csrf_protect, member_r, member_w, redirect, render

router = APIRouter()

ERROR_CODES = {"invalid_choice", "stale_question", "not_in_test", "not_in_task", "already_answered", "locked",
               "already_done", "not_finished", "no_questions", "placement_first", "question_unavailable", "no_plan"}
QUIET_CODES = {"already_answered", "already_done"}  # a double tap / refresh: say it gently, not as an error
TEST_PAGES = {"pretest": "/placement", "posttest": "/posttest"}
KIND = {"pretest": "placement", "posttest": "posttest"}  # locale namespace + template wording


def _to_int(value: str) -> int | None:
    value = (value or "").strip()
    if not value or len(value) > 9 or not value.isascii() or not value.lstrip("-").isdigit():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _fail(code: str, url: str):
    key = code if code in ERROR_CODES else "generic"
    return redirect(url, flash=f"learn.err.{key}", level="info" if code in QUIET_CODES else "error")


def _locale(request: Request, user: User) -> str:
    return i18n.pick_locale(request, user.locale)


def _rest_ctx(user: User) -> dict:
    return {"rest_until": user.paused_until, "rest_open": user.paused_until == FAR_FUTURE}


# ----------------------------------------------------------------------------- Today
@router.get("/today")
def today(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    return render(request, "learn/today.html", {"v": learning.today_view(db, user), **_rest_ctx(user)}, user=user)


# ----------------------------------------------------------------------------- placement test and post-test
def _question_page(request: Request, db: Session, user: User, stage: str, state: learning.TestState):
    q = state.next
    nonce = learning.attempt_nonce(db, user, q.id)
    options = learning.option_views(questions.shuffled_options(q, user.id, nonce))
    return render(request, "learn/test_question.html", {
        "kind": KIND[stage], "q": q, "nonce": nonce, "options": options, "number": state.answered_count + 1,
        "total": state.total, "action": f"{TEST_PAGES[stage]}/answer"}, user=user)


def _test_page(request: Request, db: Session, user: User, stage: str):
    if stage == "posttest":
        if user.pretest_done_at is None:
            return redirect("/placement")
        if user.posttest_done_at is None and not learning.posttest_available(user):
            return render(request, "learn/posttest_locked.html", {"opens": learning.posttest_open_date(user)}, user=user)
    state = learning.test_state(db, user, stage)
    finished = user.pretest_done_at if stage == "pretest" else user.posttest_done_at
    if finished:
        pre = learning.test_score(db, user, "pretest")
        if stage == "pretest":
            return render(request, "learn/placement_result.html", {
                "score": pre, "level": user.level, "few": 0 < pre.total < learning.TEST_SIZE}, user=user)
        return render(request, "learn/posttest_result.html", {"pre": pre, "post": learning.test_score(db, user, stage)},
                      user=user)
    if state.next is not None:
        return _question_page(request, db, user, stage, state)
    if stage == "posttest" and state.total == 0:
        return render(request, "learn/posttest_unavailable.html", {}, user=user)
    return render(request, "learn/test_ready.html", {"kind": KIND[stage], "empty": state.total == 0,
                                                       "action": f"{TEST_PAGES[stage]}/answer"}, user=user)


def _test_answer(stage: str, question_id: str, nonce: str, choice: str, db: Session, user: User):
    url = TEST_PAGES[stage]
    try:
        if not question_id.strip():  # the "nothing left to answer" button (or a forged empty post)
            learning.complete_test(db, user, stage)
        else:
            learning.answer_test(db, user, stage, question_id.strip()[:12], _to_int(nonce), _to_int(choice))
    except CoachError as exc:
        return _fail(exc.code, url)
    return redirect(url)


@router.get("/placement")
def placement(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    return _test_page(request, db, user, "pretest")


@router.post("/placement/answer")
def placement_answer(_=Depends(csrf_protect), question_id: str = Form(""), nonce: str = Form(""), choice: str = Form(""),
                     db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    return _test_answer("pretest", question_id, nonce, choice, db, user)


@router.get("/posttest")
def posttest(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    return _test_page(request, db, user, "posttest")


@router.post("/posttest/answer")
def posttest_answer(_=Depends(csrf_protect), question_id: str = Form(""), nonce: str = Form(""), choice: str = Form(""),
                    db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    return _test_answer("posttest", question_id, nonce, choice, db, user)


# ----------------------------------------------------------------------------- daily task
@router.get("/task")
def task(request: Request, feedback: str = "", db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    try:
        row = learning.get_or_build_task(db, user)
    except CoachError as exc:
        if exc.code == "placement_first":
            return redirect("/placement")
        if exc.code == "resting":
            return render(request, "learn/rest.html", _rest_ctx(user), user=user)
        return _fail(exc.code, "/today")
    if row is None:
        return render(request, "learn/task_empty.html", {}, user=user)

    view = learning.task_view(db, row)
    learning.finalize_task(db, user, view)  # a task whose last open question was withdrawn is finished, not stuck
    plan = user.plan
    sessions = max(1, plan.sessions_per_day or 1)
    ctx = {"view": view, "total": view.total, "sessions": sessions,
           "session_minutes": max(1, row.planned_minutes // sessions), "focus_minutes": plan.focus_minutes}

    attempt = view.answered.get(feedback[:12]) if feedback else None
    if attempt is not None and any(q.id == attempt.question_id for q in view.questions):
        q = next(q for q in view.questions if q.id == attempt.question_id)
        correct_text = (q.options or [""])[q.correct_index] if 0 <= q.correct_index < len(q.options or []) else ""
        return render(request, "learn/task_feedback.html", {
            **ctx, "q": q, "attempt": attempt, "correct_text": correct_text,
            "selected_zh": learning.has_cjk(attempt.selected_text), "correct_zh": learning.has_cjk(correct_text),
            "explanation": questions.localized_explanation(q, _locale(request, user)),
            "finished": row.status == "done"}, user=user)

    if row.status == "done":
        return render(request, "learn/task_done.html", ctx, user=user)

    q = view.current
    nonce = learning.attempt_nonce(db, user, q.id)
    options = learning.option_views(questions.shuffled_options(q, user.id, nonce))
    return render(request, "learn/task_question.html", {
        **ctx, "q": q, "nonce": nonce, "options": options, "number": view.answered_count + 1,
        "task_date": row.task_date.isoformat()}, user=user)


@router.post("/task/answer")
def task_answer(_=Depends(csrf_protect), question_id: str = Form(""), nonce: str = Form(""), choice: str = Form(""),
                db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    if user.pretest_done_at is None:
        return redirect("/placement")
    if coach.is_paused(user):
        return redirect("/today")
    row = learning.task_for_day(db, user.id, local_today(user.timezone))
    if row is None:
        return redirect("/task")
    qid = question_id.strip()[:12]
    try:
        learning.answer_task_question(db, user, row, qid, _to_int(nonce), _to_int(choice))
    except CoachError as exc:
        return _fail(exc.code, "/task")
    return redirect(f"/task?feedback={quote(qid, safe='')}")


# ----------------------------------------------------------------------------- progress
@router.get("/progress")
def progress(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    return render(request, "learn/progress.html", {"r": learning.progress_report(db, user)}, user=user)
