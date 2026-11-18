"""Slice C: "I'm stuck" -> evidence-based proposal -> the learner decides. Also rest and the plan page.

The business rules are in services/coach.py and services/plans.py; this module validates input, calls them,
and turns the outcome into screens. Three things worth knowing:

* POST /stuck uses a read-only session for the dependency and opens its OWN short write session, so the database
  write lock is never held while an external AI service is being called. The AI wording (optional, consent-gated)
  is stored afterwards in a second short write session. Any AI problem is silent: the template wording is used.
* Nothing here changes a plan except POST /proposal/{id}/decide (the learner's explicit choice) and POST /plan
  (the learner's own edit). GET handlers never write.
* /rest and /resume depend only on a logged-in user (user_r/user_w): rest is never blocked by billing or onboarding.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as dbmod
from .. import i18n
from ..config import get_settings
from ..db import get_db, get_wdb
from ..domain import actions
from ..models import Feedback, PlanChange, Recommendation, User
from ..services import coach, coach_text, consent, llm, plans
from ..services.plans import CoachError
from ..timeutil import local_today
from ..web import csrf_protect, member_r, member_w, redirect, render, user_r, user_w

log = logging.getLogger("huayu.coach")
router = APIRouter()

MAX_AI_TEXT = 900
MAX_ITEM_IDS = 20
REST_CHOICES: dict[str, int | None] = {"1": 1, "3": 3, "7": 7, "until_back": None}
DECIDED = ("accepted", "partial", "rejected")


# ----------------------------------------------------------------------------- small helpers
def _locale(request: Request, user: User) -> str:
    return i18n.pick_locale(request, user.locale)


def _err_key(code: str) -> str:
    """CoachError code -> flash key. Codes look like "stale_plan" or "invalid_item:must_reduce_new_grammar"."""
    base = code.split(":", 1)[0]
    key = f"coach.err.{base}"
    return key if key in i18n.catalog("en") else "coach.err.generic"


def _fail(url: str, code: str):
    return redirect(url, flash=_err_key(code), level="error")


def _parse_int(raw: str | None) -> int | None:
    s = (raw or "").strip()
    return int(s) if re.fullmatch(r"[0-9]{1,6}", s) else None


def _clean_note(raw: str | None, too_long_code: str) -> str | None:
    """Free text: normalise newlines, refuse (not silently cut) anything over the limit. Control chars are dropped later
    by coach.clean_text."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > coach.MAX_SELF_REPORT:
        raise CoachError(too_long_code)
    return text or None


def _own_recommendation(db: Session, user: User, rec_id: int) -> Recommendation | None:
    """None for an unknown id and for somebody else's: the two are indistinguishable on purpose (both are a 404)."""
    rec = db.get(Recommendation, rec_id) if 0 < rec_id < 2**63 else None
    return rec if rec is not None and rec.user_id == user.id else None


def _not_found(request: Request, user: User):
    """Rendered, not raised: raising from a write handler rolls the session back, which expires `user`, and the
    error page could then no longer be drawn for a logged-in learner."""
    return render(request, "error.html", {"code": 404}, status=404, user=user)


def _plan_when_made(rec: Recommendation, user: User) -> dict[str, Any] | None:
    """The plan numbers the proposal was computed from (so old -> new stays true after the plan changes)."""
    snap = (rec.interruption.context or {}).get("plan") if rec.interruption else None
    if isinstance(snap, dict):
        return snap
    return plans.snapshot(user.plan) if user.plan else None


def _item_views(rec: Recommendation, locale: str, plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    views = []
    for item in rec.items:
        facts = [text for fact in (item.evidence or []) if (text := coach_text.describe_evidence(locale, fact))]
        topic = item.params.get("topic") if item.action_code == "review_topic" and isinstance(item.params, dict) else None
        views.append({"id": item.id, "status": item.status, "topic": topic if isinstance(topic, str) else None,
                      "title": coach_text.describe_title(locale, item.action_code),
                      "text": coach_text.describe_item(locale, item.action_code, item.params, plan), "evidence": facts})
    return views


# ----------------------------------------------------------------------------- /stuck
@router.get("/stuck")
def stuck_page(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    return render(request, "coach/stuck.html", {"reasons": actions.REASONS, "max_note": coach.MAX_SELF_REPORT,
                                                "pending": coach.pending_recommendation(db, user)}, user=user)


def _add_ai_wording(rec_id: int, payload: dict[str, Any], locale: str) -> None:
    """Optional AI re-wording. Runs AFTER the proposal is committed, with NO database connection and no write
    transaction held (a slow provider must not exhaust the connection pool or block other writers).
    Only structured data goes out (never the learner's note); every failure leaves the template wording in place."""
    try:
        text = llm.explain(payload["reason"], payload["items"], payload["plan"], locale)
        text = text.strip()[:MAX_AI_TEXT] if isinstance(text, str) else ""
        if not text:
            return
        with dbmod.write_session() as wdb:
            rec = wdb.get(Recommendation, rec_id)
            if rec is not None and rec.status == "pending":
                rec.explanation, rec.explanation_source = text, "llm"
    except Exception as exc:  # noqa: BLE001 - the AI is a nicety, never a reason to fail the request
        log.warning("ai wording skipped: %s", type(exc).__name__)


@router.post("/stuck")
def stuck_submit(request: Request, _: None = Depends(csrf_protect), reason: str = Form(""), note: str = Form(""),
                 available_minutes: str = Form(""), db: Session = Depends(get_db), user: User = Depends(member_r)):
    try:
        if reason not in actions.REASONS:
            raise CoachError("invalid_reason")
        minutes = None
        if reason == "no_time" and available_minutes.strip():
            minutes = _parse_int(available_minutes)
            if minutes is None or not 5 <= minutes <= 240:
                raise CoachError("invalid_available_minutes")
        text = _clean_note(note, "note_too_long")
        uid, locale = user.id, _locale(request, user)
        ai_allowed = get_settings().llm_provider != "none" and consent.llm_allowed(db, user)
        db.close()  # give the read connection back BEFORE the write session (and any AI call) needs one
        with dbmod.write_session() as wdb:  # short transaction: proposal only, no network inside
            writer = wdb.get(User, uid)
            rec = coach.report_interruption(wdb, writer, reason, text, minutes)
            rec_id = rec.id
            payload = {"reason": reason, "plan": plans.snapshot(writer.plan),
                       "items": [{"action": i.action_code, "params": i.params, "evidence": i.evidence} for i in rec.items]}
    except CoachError as exc:
        return _fail("/stuck", exc.code)
    if ai_allowed:
        _add_ai_wording(rec_id, payload, locale)
    return redirect(f"/proposal/{rec_id}")


# ----------------------------------------------------------------------------- /proposal/{id}
@router.get("/proposal/{rec_id:int}")
def proposal_page(rec_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    rec = _own_recommendation(db, user, rec_id)
    if rec is None:
        return _not_found(request, user)
    locale = _locale(request, user)
    reason = rec.interruption.reason_code
    stale = rec.status == "pending" and (user.plan is None or user.plan.version != rec.base_plan_version)
    newer = None
    if rec.status == "superseded" or stale:
        newer = next((p for p in [coach.pending_recommendation(db, user)] if p and p.id != rec.id), None)
    ai = rec.explanation if rec.explanation_source == "llm" and rec.explanation else None
    view = {"id": rec.id, "status": rec.status, "stale": stale, "newer_id": newer.id if newer else None,
            "reason": reason, "reason_title": coach_text.reason_title(locale, reason),
            "ai_text": ai, "summary": coach_text.summarize(locale, reason),
            "glance": coach_text.glance(locale, [i.action_code for i in rec.items]),
            "cards": _item_views(rec, locale, _plan_when_made(rec, user))}
    return render(request, "coach/proposal.html", {"view": view}, user=user)


@router.post("/proposal/{rec_id:int}/decide")
def proposal_decide(rec_id: int, request: Request, _: None = Depends(csrf_protect), decision: str = Form(""),
                    item_id: list[str] = Form(default=[]), db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    rec = _own_recommendation(db, user, rec_id)
    if rec is None:
        return _not_found(request, user)
    back = f"/proposal/{rec_id}"
    try:
        if decision == "reject":
            coach.decide(db, user, rec_id, "reject")
        elif decision == "accept_all":
            coach.decide(db, user, rec_id, "accept", [i.id for i in rec.items])
        elif decision == "accept_selected":
            if len(item_id) > MAX_ITEM_IDS:
                raise CoachError("item_not_in_recommendation")
            ids = [_parse_int(raw) for raw in item_id]
            if any(i is None for i in ids):
                raise CoachError("item_not_in_recommendation")
            coach.decide(db, user, rec_id, "accept", ids)
        else:
            raise CoachError("invalid_decision")
    except CoachError as exc:
        db.rollback()
        if exc.code == "already_processed":  # double tap, or a second tab: harmless, just show the result
            return redirect(f"{back}/done")
        return _fail(back, exc.code)
    return redirect(f"{back}/done")


@router.get("/proposal/{rec_id:int}/done")
def proposal_done(rec_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    rec = _own_recommendation(db, user, rec_id)
    if rec is None:
        return _not_found(request, user)
    if rec.status == "pending":
        return redirect(f"/proposal/{rec_id}")
    locale = _locale(request, user)
    change = db.scalars(select(PlanChange).where(PlanChange.user_id == user.id, PlanChange.recommendation_id == rec.id)
                        .order_by(PlanChange.id.desc())).first()
    lines = coach_text.describe_plan_change(locale, change.old_values, change.new_values) if change else []
    numeric = [ln for ln in lines if ln["field"] != "rest_until"]
    rest_until = change.new_values.get("rest_until") if change and isinstance(change.new_values, dict) else None
    rest_line = coach_text.format_date(locale, rest_until) if rest_until else None
    if rec.status == "superseded":
        kind = "superseded"
    elif rec.status == "rejected":
        kind = "rejected"
    elif numeric:
        kind = "changed"
    elif rest_line:
        kind = "rest"
    else:
        kind = "nochange"  # never claim "plan updated" when no plan number moved
    items = _item_views(rec, locale, _plan_when_made(rec, user))
    rated = db.scalar(select(Feedback.id).where(Feedback.recommendation_id == rec.id, Feedback.user_id == user.id)) is not None
    view = {"id": rec.id, "status": rec.status, "kind": kind, "changes": numeric, "rest_date": rest_line,
            "version": change.new_version if change and numeric else None, "rated": rated,
            "can_rate": rec.status in DECIDED, "left_out": [i for i in items if i["status"] == "rejected"] if rec.status == "partial" else [],
            "newer_id": None}
    if kind == "superseded":
        newer = coach.pending_recommendation(db, user)
        view["newer_id"] = newer.id if newer and newer.id != rec.id else None
    return render(request, "coach/done.html", {"view": view}, user=user)


@router.post("/proposal/{rec_id:int}/feedback")
def proposal_feedback(rec_id: int, request: Request, _: None = Depends(csrf_protect), clarity: str = Form(""),
                      helpfulness: str = Form(""), pressure: str = Form(""), comment: str = Form(""),
                      db: Session = Depends(get_wdb), user: User = Depends(member_w)):
    rec = _own_recommendation(db, user, rec_id)
    if rec is None:
        return _not_found(request, user)
    back = f"/proposal/{rec_id}" if rec.status == "pending" else f"/proposal/{rec_id}/done"
    try:
        if rec.status in ("pending", "superseded"):
            raise CoachError("decide_first")
        ratings = [_parse_int(clarity), _parse_int(helpfulness), _parse_int(pressure)]
        if any(r is None for r in ratings):
            raise CoachError("invalid_rating")
        coach.save_feedback(db, user, rec_id, *ratings, comment=_clean_note(comment, "comment_too_long"))
    except CoachError as exc:
        db.rollback()
        return _fail(back, exc.code)
    return redirect(back, flash="coach.flash.thanks", level="success")


# ----------------------------------------------------------------------------- /rest, /resume (never blocked)
@router.get("/rest")
def rest_page(request: Request, user: User = Depends(user_r)):
    paused = coach.is_paused(user)
    open_ended = paused and user.paused_until == actions.FAR_FUTURE
    until = coach_text.format_date(_locale(request, user), user.paused_until) if paused and not open_ended else None
    return render(request, "coach/rest.html", {"paused": paused, "open_ended": open_ended, "until": until}, user=user)


@router.post("/rest")
def rest_start(request: Request, _: None = Depends(csrf_protect), days: str = Form(""), db: Session = Depends(get_wdb),
               user: User = Depends(user_w)):
    if days not in REST_CHOICES:
        return _fail("/rest", "invalid_rest_days")
    try:
        until = coach.pause(db, user, REST_CHOICES[days])
    except CoachError as exc:
        db.rollback()
        return _fail("/rest", exc.code)
    if REST_CHOICES[days] is None:
        return redirect("/rest", flash="coach.flash.rest_started_open", level="success")
    return redirect("/rest", flash="coach.flash.rest_started", level="success",
                    date=coach_text.format_date(_locale(request, user), until))


@router.post("/resume")
def resume(_: None = Depends(csrf_protect), db: Session = Depends(get_wdb), user: User = Depends(user_w)):
    coach.resume(db, user)
    return redirect("/today", flash="coach.flash.welcome_back", level="success")


# ----------------------------------------------------------------------------- /plan
def _history_view(locale: str, user: User, pc: PlanChange) -> dict[str, Any]:
    if pc.source == "default":
        v = pc.new_values or {}
        who, lines = "who_default", [i18n.t(locale, "coach.history.default_text", minutes=v.get("daily_minutes", "?"),
                                              grammar=v.get("new_grammar", "?"), exercises=v.get("exercise_count", "?"))]
    else:
        if pc.result == "reject":
            who = "who_reject"
        elif pc.source == "learner":
            who = "who_learner"
        else:
            who = "who_partial" if pc.result == "partial" else "who_proposal"
        lines = [ln["text"] for ln in coach_text.describe_plan_change(locale, pc.old_values, pc.new_values)]
        if not lines:
            lines = [i18n.t(locale, "coach.history.nothing")]
    return {"who": who, "lines": lines, "when": coach_text.format_date(locale, local_today(user.timezone, pc.created_at)),
            "done_url": f"/proposal/{pc.recommendation_id}/done" if pc.recommendation_id else None}


@router.get("/plan")
def plan_page(request: Request, db: Session = Depends(get_db), user: User = Depends(member_r)):
    locale = _locale(request, user)
    plan = user.plan
    topics, history = [], []
    if plan is not None:
        state = plans.to_state(plan, local_today(user.timezone))
        topics = [{"topic": topic, "until": coach_text.format_date(locale, until)} for topic, until in state.review_topics]
        rows = db.scalars(select(PlanChange).where(PlanChange.user_id == user.id).order_by(PlanChange.id.desc()).limit(30))
        history = [_history_view(locale, user, pc) for pc in rows]
    pending = coach.pending_recommendation(db, user)
    return render(request, "coach/plan.html", {"plan": plan, "topics": topics, "history": history,
                                               "pending_id": pending.id if pending else None,
                                               "limits": {"daily": (10, 90), "grammar": (1, 10), "exercises": (3, 20)}},
                  user=user)


@router.post("/plan")
def plan_save(_: None = Depends(csrf_protect), daily_minutes: str = Form(""), new_grammar: str = Form(""),
              exercise_count: str = Form(""), expected_version: str = Form(""), db: Session = Depends(get_wdb),
              user: User = Depends(member_w)):
    try:
        values = {"daily_minutes": _parse_int(daily_minutes), "new_grammar": _parse_int(new_grammar),
                  "exercise_count": _parse_int(exercise_count)}
        for name, value in values.items():
            if value is None:
                raise CoachError(f"invalid_{name}")
        version = _parse_int(expected_version)
        if version is None:
            raise CoachError("invalid_form")
        change = plans.manual_update(db, user, expected_version=version, **values)
    except CoachError as exc:
        db.rollback()
        return _fail("/plan", exc.code)
    return redirect("/plan", flash="coach.flash.plan_saved", level="success", version=change.new_version)
