"""Learning flows: placement / post-test, the daily task, and the progress report.

Routers stay thin; every rule lives here so it can be tested without HTTP.

Rules worth knowing (each is covered by tests/test_learn.py):
* One answer per question: a test question (per stage) or a task question (per task) accepts exactly one attempt;
  a double tap is refused with CoachError("already_answered"), never stored twice.
* The page and the answer check recompute the same option shuffle from (user, question, nonce). The nonce is
  "how many times this learner has answered this question before", so a repeated question appears in a new order
  while a page reload shows the same order. The client posts the nonce and the option INDEX; the server never trusts
  a posted "correct" flag.
* Only servable questions are ever shown (services.questions.pool); a question flagged later is dropped from an
  unfinished task, but answers already given still count.
* A task belongs to (user, local day). It is rebuilt from a changed plan only while nothing was answered yet.
* Percentages are quoted only with at least MIN_ATTEMPTS_FOR_PERCENT answers behind them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain import actions, scheduler
from ..domain.diagnosis import MIN_ATTEMPTS_FOR_PERCENT
from ..domain.levels import estimate_level
from ..models import Interruption, PlanChange, Question, QuizAttempt, StudyPlan, StudyTask, User
from ..timeutil import local_midnight_utc, local_today, utcnow
from . import coach, questions
from .plans import CoachError, to_state

TEST_SIZE = 10            # questions in the placement test and in the post-test
TEST_A2_SHARE = 4         # when the bank is larger than TEST_SIZE, up to this many are A2 (so A2 can be detected)
POSTTEST_WAIT_DAYS = 7
WINDOW_DAYS = 7
SKILLS = ("vocab", "grammar", "reading")
TEST_STAGES = ("pretest", "posttest")
PLAN_NUMBER_FIELDS = ("daily_minutes", "new_grammar", "exercise_count", "sessions_per_day", "focus_minutes")


def has_cjk(text: str) -> bool:
    return any("぀" <= ch <= "鿿" or "㄀" <= ch <= "ㄯ" for ch in text)


# ============================================================================ recording one answer
def attempt_nonce(db: Session, user: User, question_id: str) -> int:
    """How many times this learner has answered this question so far (drives the option shuffle)."""
    return db.scalar(select(func.count()).select_from(QuizAttempt)
                     .where(QuizAttempt.user_id == user.id, QuizAttempt.question_id == question_id)) or 0


def option_views(options: list[str]) -> list[dict[str, Any]]:
    return [{"index": i, "text": text, "zh": has_cjk(text)} for i, text in enumerate(options)]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def record_attempt(db: Session, user: User, question: Question, nonce: Any, choice: Any, stage: str,
                   task_id: int | None = None, now: datetime | None = None) -> QuizAttempt:
    """Validate (nonce, option index) against the server-side shuffle and store ONE attempt."""
    options = list(question.options or [])
    if len(options) < 2 or not _is_int(question.correct_index) or not 0 <= question.correct_index < len(options):
        raise CoachError("question_unavailable")
    if not _is_int(choice) or not 0 <= choice < len(options):
        raise CoachError("invalid_choice")
    if not _is_int(nonce) or nonce != attempt_nonce(db, user, question.id):
        raise CoachError("stale_question")
    selected = questions.shuffled_options(question, user.id, nonce)[choice]
    attempt = QuizAttempt(user_id=user.id, question_id=question.id, stage=stage, task_id=task_id,
                          selected_text=selected, is_correct=selected == options[question.correct_index],
                          skill=question.skill, topic=question.topic, created_at=now or utcnow())
    db.add(attempt)
    db.flush()
    return attempt


# ============================================================================ placement test and post-test
@dataclass(frozen=True)
class Score:
    correct: int
    total: int

    @property
    def percent(self) -> int | None:
        return round(100 * self.correct / self.total) if self.total >= MIN_ATTEMPTS_FOR_PERCENT else None


@dataclass
class TestState:
    stage: str
    questions: list[Question]
    answered: dict[str, QuizAttempt]  # question id -> the learner's attempt in this stage

    @property
    def total(self) -> int:
        return len(self.questions)

    @property
    def answered_count(self) -> int:
        return sum(1 for q in self.questions if q.id in self.answered)

    @property
    def next(self) -> Question | None:
        return next((q for q in self.questions if q.id not in self.answered), None)


def select_test_questions(pool: list[Question], size: int = TEST_SIZE) -> list[Question]:
    """Up to `size` questions, easy (A1) first. A big bank keeps some A2 items so the test can tell the levels apart."""
    if len(pool) > size:
        a1 = [q for q in pool if q.level != "A2"]
        a2 = [q for q in pool if q.level == "A2"]
        n_a2 = min(len(a2), TEST_A2_SHARE)
        chosen = a1[: size - n_a2] + a2[:n_a2]
        chosen += [q for q in pool if q not in chosen][: size - len(chosen)]
    else:
        chosen = list(pool)
    return sorted(chosen, key=lambda q: (q.level, q.id))


def test_state(db: Session, user: User, stage: str) -> TestState:
    qs = select_test_questions(questions.pool(db, stage, None))
    attempts = {a.question_id: a for a in db.scalars(
        select(QuizAttempt).where(QuizAttempt.user_id == user.id, QuizAttempt.stage == stage).order_by(QuizAttempt.id))}
    return TestState(stage, qs, attempts)


def test_score(db: Session, user: User, stage: str) -> Score:
    rows = db.scalars(select(QuizAttempt.is_correct).where(QuizAttempt.user_id == user.id,
                                                           QuizAttempt.stage == stage)).all()
    return Score(sum(1 for ok in rows if ok), len(rows))


def posttest_open_date(user: User) -> date | None:
    if user.pretest_done_at is None:
        return None
    return local_today(user.timezone, user.pretest_done_at) + timedelta(days=POSTTEST_WAIT_DAYS)


def posttest_available(user: User, now: datetime | None = None) -> bool:
    opens = posttest_open_date(user)
    return opens is not None and local_today(user.timezone, now) >= opens


def _check_test_open(user: User, stage: str, now: datetime | None) -> None:
    if stage not in TEST_STAGES:
        raise ValueError(stage)
    if stage == "pretest":
        if user.pretest_done_at is not None:
            raise CoachError("already_done")
        return
    if user.pretest_done_at is None:
        raise CoachError("placement_first")
    if user.posttest_done_at is not None:
        raise CoachError("already_done")
    if not posttest_available(user, now):
        raise CoachError("locked")


def _finish_test(db: Session, user: User, state: TestState, now: datetime) -> None:
    if state.stage == "pretest":
        rows = db.execute(select(Question.level, QuizAttempt.is_correct).join(Question, Question.id == QuizAttempt.question_id)
                          .where(QuizAttempt.user_id == user.id, QuizAttempt.stage == "pretest")).all()
        user.level = estimate_level([(level, bool(ok)) for level, ok in rows])  # no answers at all -> A1
        user.pretest_done_at = now
    else:
        if not state.questions:
            raise CoachError("no_questions")
        user.posttest_done_at = now


def answer_test(db: Session, user: User, stage: str, question_id: str, nonce: Any, choice: Any,
                now: datetime | None = None) -> bool:
    """Record one test answer. Returns True when this was the last question and the test is now finished."""
    now = now or utcnow()
    _check_test_open(user, stage, now)
    state = test_state(db, user, stage)
    question = next((q for q in state.questions if q.id == question_id), None)
    if question is None:
        raise CoachError("not_in_test")
    if question.id in state.answered:
        raise CoachError("already_answered")
    state.answered[question.id] = record_attempt(db, user, question, nonce, choice, stage, None, now)
    if state.next is None:
        _finish_test(db, user, state, now)
        return True
    return False


def complete_test(db: Session, user: User, stage: str, now: datetime | None = None) -> None:
    """Finish a test with nothing left to answer (also the "no questions available" case: level A1 for placement)."""
    now = now or utcnow()
    _check_test_open(user, stage, now)
    state = test_state(db, user, stage)
    if state.next is not None:
        raise CoachError("not_finished")
    _finish_test(db, user, state, now)


# ============================================================================ the daily task
@dataclass
class TaskView:
    task: StudyTask
    questions: list[Question]            # in task order, minus unanswered ones that are no longer servable
    answered: dict[str, QuizAttempt]     # this task's attempts by question id

    @property
    def total(self) -> int:
        return len(self.questions)

    @property
    def answered_count(self) -> int:
        return sum(1 for q in self.questions if q.id in self.answered)

    @property
    def correct_count(self) -> int:
        return sum(1 for q in self.questions if q.id in self.answered and self.answered[q.id].is_correct)

    @property
    def current(self) -> Question | None:
        return next((q for q in self.questions if q.id not in self.answered), None)


def task_for_day(db: Session, user_id: int, day: date) -> StudyTask | None:
    return db.scalar(select(StudyTask).where(StudyTask.user_id == user_id, StudyTask.task_date == day))


def task_view(db: Session, task: StudyTask) -> TaskView:
    ids = [str(i) for i in (task.question_ids or [])]
    rows = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(ids)))} if ids else {}
    answered = {a.question_id: a for a in db.scalars(
        select(QuizAttempt).where(QuizAttempt.task_id == task.id).order_by(QuizAttempt.id))}
    servable = questions.servable_statuses()
    kept = [rows[i] for i in ids if i in rows and (i in answered or (rows[i].active and rows[i].review_status in servable))]
    return TaskView(task, kept, answered)


def _history(db: Session, user: User, ids: list[str]) -> dict[str, scheduler.History]:
    rows = db.execute(select(QuizAttempt.question_id, QuizAttempt.is_correct, QuizAttempt.created_at)
                      .where(QuizAttempt.user_id == user.id, QuizAttempt.question_id.in_(ids))
                      .order_by(QuizAttempt.created_at, QuizAttempt.id)).all()
    out: dict[str, scheduler.History] = {}
    for qid, ok, at in rows:
        prev = out.get(qid)
        out[qid] = scheduler.History(attempts=(prev.attempts if prev else 0) + 1, last_correct=bool(ok),
                                     last_seen=at.isoformat())
    return out


def _pick_ids(db: Session, user: User, plan: StudyPlan, today: date) -> list[str]:
    pool = questions.pool(db, "daily", user.level)
    if not pool:
        return []
    infos = [scheduler.QInfo(q.id, q.skill, q.topic, q.level) for q in pool]
    review = [topic for topic, _ in to_state(plan, today).review_topics]
    return scheduler.pick_questions(infos, _history(db, user, [q.id for q in pool]), plan.exercise_count,
                                    plan.new_grammar, review, user.id, today)


def _has_answers(db: Session, task: StudyTask) -> bool:
    return bool(db.scalar(select(func.count()).select_from(QuizAttempt).where(QuizAttempt.task_id == task.id)))


def needs_rebuild(db: Session, task: StudyTask, plan: StudyPlan) -> bool:
    """A pending task nobody has touched is rebuilt when the plan moved on (or nothing in it can be served any more)."""
    if task.status != "pending" or _has_answers(db, task):
        return False
    return task.plan_version != plan.version or not task_view(db, task).questions


def _insert_task(db: Session, task: StudyTask) -> StudyTask:
    """Insert, tolerating a concurrent request that built the same day's task first (unique per user and day)."""
    try:
        with db.begin_nested():
            db.add(task)
            db.flush()
        return task
    except IntegrityError:
        existing = task_for_day(db, task.user_id, task.task_date)
        if existing is None:
            raise
        return existing


def _guard_can_study(user: User, now: datetime) -> StudyPlan:
    if coach.is_paused(user, now):
        raise CoachError("resting")
    if user.pretest_done_at is None:
        raise CoachError("placement_first")
    if user.plan is None:
        raise CoachError("no_plan")
    return user.plan


def get_or_build_task(db: Session, user: User, now: datetime | None = None) -> StudyTask | None:
    """Today's task for this learner, built from the current plan if it does not exist yet.
    Returns None when there is nothing servable. Raises CoachError("resting" | "placement_first" | "no_plan")."""
    now = now or utcnow()
    plan = _guard_can_study(user, now)
    today = local_today(user.timezone, now)
    task = task_for_day(db, user.id, today)
    if task is not None:
        if not needs_rebuild(db, task, plan):
            return task
        ids = _pick_ids(db, user, plan, today)
        if not ids:
            return None
        task.question_ids, task.planned_minutes, task.plan_version = ids, plan.daily_minutes, plan.version
        return task
    ids = _pick_ids(db, user, plan, today)
    if not ids:
        return None
    day_no = (db.scalar(select(func.count()).select_from(StudyTask).where(StudyTask.user_id == user.id)) or 0) + 1
    return _insert_task(db, StudyTask(user_id=user.id, task_date=today, day_no=day_no, question_ids=ids,
                                      planned_minutes=plan.daily_minutes, plan_version=plan.version, status="pending",
                                      created_at=now))


def finalize_task(db: Session, user: User, view: TaskView, now: datetime | None = None) -> bool:
    """When every question is answered: mark the task done and tell the coach the learner studied again."""
    now = now or utcnow()
    task = view.task
    if task.status == "done" or not view.questions or view.current is not None:
        return False
    task.status, task.completed_at = "done", now
    coach.mark_resumed(db, user, now)  # "came back after an interruption" is measured here
    return True


def answer_task_question(db: Session, user: User, task: StudyTask, question_id: str, nonce: Any, choice: Any,
                         now: datetime | None = None) -> tuple[QuizAttempt, bool]:
    """Record one answer of today's task. Returns (attempt, task_is_now_done)."""
    now = now or utcnow()
    view = task_view(db, task)
    question = next((q for q in view.questions if q.id == question_id), None)
    if question is None:
        raise CoachError("not_in_task")
    if question.id in view.answered:
        raise CoachError("already_answered")
    attempt = record_attempt(db, user, question, nonce, choice, "daily", task.id, now)
    view.answered[question.id] = attempt
    return attempt, finalize_task(db, user, view, now)


# ============================================================================ the "Today" page
@dataclass
class TaskCard:
    status: str                      # new | started | done | empty
    minutes: int
    exercises: int
    answered: int
    new_grammar: int | None          # None when the task was built from an older plan
    sessions: int
    session_minutes: int
    focus_minutes: int | None
    plan_changed: bool               # the plan changed after today's task was started


@dataclass
class WeekSummary:
    answers: int = 0
    correct: int = 0
    tasks_done: int = 0


@dataclass
class TodayView:
    state: str                       # resting | placement | noplan | task
    week: WeekSummary
    rest_until: date | None = None
    rest_open_ended: bool = False
    card: TaskCard | None = None
    posttest_ready: bool = False


def window_start(user: User, today: date) -> datetime:
    return local_midnight_utc(today - timedelta(days=WINDOW_DAYS - 1), user.timezone)


def week_summary(db: Session, user: User, now: datetime | None = None) -> WeekSummary:
    now = now or utcnow()
    today = local_today(user.timezone, now)
    flags = db.scalars(select(QuizAttempt.is_correct).where(  # daily practice only: placement/post-test have their own screens
        QuizAttempt.user_id == user.id, QuizAttempt.stage == "daily", QuizAttempt.created_at >= window_start(user, today))).all()
    done = db.scalar(select(func.count()).select_from(StudyTask).where(
        StudyTask.user_id == user.id, StudyTask.status == "done",
        StudyTask.task_date >= today - timedelta(days=WINDOW_DAYS - 1))) or 0
    return WeekSummary(len(flags), sum(1 for ok in flags if ok), done)


def _card(db: Session, user: User, plan: StudyPlan, today: date) -> TaskCard:
    sessions = max(1, plan.sessions_per_day or 1)
    task = task_for_day(db, user.id, today)
    if task is not None and not needs_rebuild(db, task, plan):
        view = task_view(db, task)
        status = "done" if task.status == "done" else ("started" if view.answered_count else "new")
        same_plan = task.plan_version == plan.version
        return TaskCard(status, task.planned_minutes, view.total, view.answered_count,
                        plan.new_grammar if same_plan else None, sessions, max(1, task.planned_minutes // sessions),
                        plan.focus_minutes, plan_changed=not same_plan)
    exercises = min(plan.exercise_count, len(questions.pool(db, "daily", user.level)))
    return TaskCard("new" if exercises else "empty", plan.daily_minutes, exercises, 0, plan.new_grammar, sessions,
                    max(1, plan.daily_minutes // sessions), plan.focus_minutes, plan_changed=False)


def today_view(db: Session, user: User, now: datetime | None = None) -> TodayView:
    now = now or utcnow()
    today = local_today(user.timezone, now)
    week = week_summary(db, user, now)
    if coach.is_paused(user, now):
        return TodayView("resting", week, rest_until=user.paused_until,
                         rest_open_ended=user.paused_until == actions.FAR_FUTURE)
    if user.pretest_done_at is None:
        return TodayView("placement", week)
    if user.plan is None:
        return TodayView("noplan", week)
    ready = (user.posttest_done_at is None and posttest_available(user, now)
             and bool(questions.pool(db, "posttest", None)))
    return TodayView("task", week, card=_card(db, user, user.plan, today), posttest_ready=ready)


# ============================================================================ the progress report
@dataclass
class SkillRow:
    skill: str
    answers: int
    correct: int

    @property
    def percent(self) -> int | None:
        return round(100 * self.correct / self.answers) if self.answers >= MIN_ATTEMPTS_FOR_PERCENT else None


@dataclass
class ReasonRow:
    reason: str
    count: int
    resumed: int


@dataclass
class ChangeRow:
    day: date
    source: str
    changes: list[dict[str, Any]]


@dataclass
class Progress:
    since: date
    today: date
    total_answers: int = 0
    skills: list[SkillRow] = field(default_factory=list)
    tasks_done: int = 0
    tasks_total: int = 0
    reasons: list[ReasonRow] = field(default_factory=list)
    interruptions: int = 0
    resumed: int = 0
    plan_changes: int = 0
    last_change: ChangeRow | None = None
    pretest: Score | None = None
    posttest: Score | None = None
    posttest_opens: date | None = None
    posttest_ready: bool = False


def _plan_diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name in (*PLAN_NUMBER_FIELDS, "review_topics"):
        if name in new and old.get(name) != new[name]:
            scalar = name in PLAN_NUMBER_FIELDS
            out.append({"field": name, "scalar": scalar, "old": old.get(name) if scalar else None,
                        "new": new[name] if scalar else None})
    return out


def progress_report(db: Session, user: User, now: datetime | None = None) -> Progress:
    """Everything shown on /progress, counted from the database for the last WINDOW_DAYS local days (today included)."""
    now = now or utcnow()
    today = local_today(user.timezone, now)
    start = window_start(user, today)
    since = today - timedelta(days=WINDOW_DAYS - 1)
    rep = Progress(since=since, today=today)

    # answers per skill
    counts: dict[str, list[int]] = {s: [0, 0] for s in SKILLS}
    for skill, ok in db.execute(select(QuizAttempt.skill, QuizAttempt.is_correct)
                                .where(QuizAttempt.user_id == user.id, QuizAttempt.created_at >= start)):
        rep.total_answers += 1
        if skill in counts:
            counts[skill][0] += 1
            counts[skill][1] += int(bool(ok))
    rep.skills = [SkillRow(s, n, c) for s, (n, c) in counts.items()]

    # tasks: today's task counts only once it is done (a task still in progress is not a miss)
    for task_date, status in db.execute(select(StudyTask.task_date, StudyTask.status).where(
            StudyTask.user_id == user.id, StudyTask.task_date >= since, StudyTask.task_date <= today)):
        if task_date < today or status == "done":
            rep.tasks_total += 1
            rep.tasks_done += int(status == "done")

    # interruptions the learner reported themselves, and whether studying followed
    by_reason: dict[str, list[int]] = {}
    for reason, status in db.execute(select(Interruption.reason_code, Interruption.status).where(
            Interruption.user_id == user.id, Interruption.source == "actual", Interruption.created_at >= start)):
        row = by_reason.setdefault(reason, [0, 0])
        row[0] += 1
        row[1] += int(status == "resumed")
    ordered = [r for r in actions.REASONS if r in by_reason] + sorted(r for r in by_reason if r not in actions.REASONS)
    rep.reasons = [ReasonRow(r, by_reason[r][0], by_reason[r][1]) for r in ordered]
    rep.interruptions = sum(r.count for r in rep.reasons)
    rep.resumed = sum(r.resumed for r in rep.reasons)

    # real plan changes only (a rejected proposal or a pause leaves the version unchanged)
    changes = db.scalars(select(PlanChange).where(
        PlanChange.user_id == user.id, PlanChange.created_at >= start, PlanChange.new_version > PlanChange.old_version,
        PlanChange.source != "default").order_by(PlanChange.created_at.desc(), PlanChange.id.desc())).all()
    rep.plan_changes = len(changes)
    if changes:
        last = changes[0]
        rep.last_change = ChangeRow(local_today(user.timezone, last.created_at), last.source,
                                    _plan_diff(last.old_values or {}, last.new_values or {}))

    # placement check vs self-check
    if user.pretest_done_at is not None:
        pre = test_score(db, user, "pretest")
        rep.pretest = pre if pre.total else None
        rep.posttest_opens = posttest_open_date(user)
        rep.posttest_ready = user.posttest_done_at is None and posttest_available(user, now)
    if user.posttest_done_at is not None:
        post = test_score(db, user, "posttest")
        rep.posttest = post if post.total else None
    return rep
