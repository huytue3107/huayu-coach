"""Slice B: placement test, daily task, post-test, progress report (services/learning.py + routers/learn.py)."""
from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db as dbmod
from app import i18n
from app.config import LOCALES, get_settings
from app.domain.actions import FAR_FUTURE
from app.models import (Interruption, PlanChange, Question, QuizAttempt, StudyPlan, StudyTask, User)
from app.services import coach, learning, questions
from app.services.plans import CoachError, manual_update
from app.timeutil import local_midnight_utc, local_today, utcnow
from tests.conftest import add_attempt, login_as, make_user, onboarded_user

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@pytest.fixture(autouse=True)
def _only_learn_router(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "learn")


# ----------------------------------------------------------------------------- helpers
def mk(*, daily_minutes=30, level="A1", locale="en", placement_done=True, onboarded=True, **kw) -> User:
    """A learner committed to the database (the web app uses its own sessions, so never leave one open)."""
    with dbmod.write_session() as s:
        u = onboarded_user(s, daily_minutes=daily_minutes, level=level, **kw)
        u.locale = locale
        if not placement_done:
            u.level, u.pretest_done_at = None, None
        if not onboarded:
            u.onboarded_at = None
        return u


def rd(fn):
    """Read in a short-lived session; results are detached (rollback would otherwise expire them)."""
    with dbmod.read_session() as s:
        out = fn(s)
        s.expunge_all()
        return out


def wr(fn):
    with dbmod.write_session() as s:
        return fn(s)


def fresh_attempts(uid, **filters):
    def q(s):
        stmt = select(QuizAttempt).where(QuizAttempt.user_id == uid).order_by(QuizAttempt.id)
        for k, v in filters.items():
            stmt = stmt.where(getattr(QuizAttempt, k) == v)
        return list(s.scalars(stmt))
    return rd(q)


def today_task(uid, tz="Asia/Taipei"):
    return rd(lambda s: s.scalar(select(StudyTask).where(StudyTask.user_id == uid, StudyTask.task_date == local_today(tz))))


def page_question(html: str) -> tuple[str, int]:
    return (re.search(r'name="question_id" value="([^"]+)"', html).group(1),
            int(re.search(r'name="nonce" value="(\d+)"', html).group(1)))


def choice_for(uid: int, qid: str, nonce: int, correct: bool) -> int:
    """Index into the shuffled list that is (or is not) the right answer; mirrors what the server recomputes."""
    q = rd(lambda s: s.get(Question, qid))
    opts = questions.shuffled_options(q, uid, nonce)
    right = opts.index(q.options[q.correct_index])
    return right if correct else next(i for i in range(len(opts)) if i != right)


def post_answer(web, csrf, path, qid, nonce, choice):
    return web.post(path, data={"csrf": csrf, "question_id": qid, "nonce": str(nonce), "choice": str(choice)})


def play_test(web, csrf, uid, page, answer_path, *, correct=True):
    """Answer every question of a test through HTTP. Returns how many were answered."""
    n = 0
    for _ in range(40):
        html = web.get(page).text
        if 'name="question_id"' not in html:
            return n
        qid, nonce = page_question(html)
        r = post_answer(web, csrf, answer_path, qid, nonce, choice_for(uid, qid, nonce, correct))
        assert r.status_code == 303 and r.headers["location"] == page
        n += 1
    raise AssertionError("test never ended")


def play_task(web, csrf, uid, *, correct=True, stop_after=None):
    """Answer today's task question by question through HTTP. Returns the number of answers."""
    n = 0
    for _ in range(40):
        if stop_after is not None and n >= stop_after:
            return n
        html = web.get("/task").text
        if 'name="question_id"' not in html:
            return n
        qid, nonce = page_question(html)
        r = post_answer(web, csrf, "/task/answer", qid, nonce, choice_for(uid, qid, nonce, correct))
        assert r.status_code == 303 and r.headers["location"].startswith("/task?feedback=")
        n += 1
    raise AssertionError("task never ended")


def mon(d: date) -> str:
    return f"{MONTHS[d.month]} {d.day}, {d.year}"


def txt(response) -> str:
    """Response text with HTML entities decoded (templates escape apostrophes)."""
    return htmllib.unescape(response.text)


def no_raw_keys(html: str) -> None:
    leaked = [k for k in i18n.catalog("en") if k.startswith("learn.") and k in html]
    assert not leaked, leaked[:5]


# ============================================================================ services: shuffle and answer recording
def test_shuffle_is_deterministic_per_user_question_and_nonce(seeded):
    q = rd(lambda s: s.get(Question, "Q001"))
    a = questions.shuffled_options(q, 7, 0)
    assert a == questions.shuffled_options(q, 7, 0) and sorted(a) == sorted(q.options)
    orders = {tuple(questions.shuffled_options(q, uid, n)) for uid in range(1, 6) for n in range(3)}
    assert len(orders) > 1  # different (user, nonce) pairs do not all give the same order


def test_recorded_correctness_matches_the_real_correct_option_for_every_index(web, seeded):
    """Four learners tap slot 0..3 of the same first question: exactly the slot holding the right text is correct."""
    results = []
    for slot in range(4):
        u = mk(placement_done=False)
        csrf = login_as(web, u)
        qid, nonce = page_question(web.get("/placement").text)
        r = post_answer(web, csrf, "/placement/answer", qid, nonce, slot)
        assert r.status_code == 303
        att = fresh_attempts(u.id)[0]
        q = rd(lambda s: s.get(Question, qid))
        shuffled = questions.shuffled_options(q, u.id, nonce)
        assert att.selected_text == shuffled[slot]
        assert att.is_correct == (shuffled[slot] == q.options[q.correct_index])
        assert (att.skill, att.topic, att.stage, att.task_id) == (q.skill, q.topic, "pretest", None)
        results.append(att.is_correct)
    assert results.count(True) == 1


def test_select_test_questions_keeps_a2_items_and_orders_easy_first():
    def mkq(i, level):
        return Question(id=f"X{i:02d}", stage="pretest", level=level, skill="vocab", question_zh="q", options=list("abcd"),
                        correct_index=0)
    pool = [mkq(i, "A1") for i in range(14)] + [mkq(20 + i, "A2") for i in range(8)]
    chosen = learning.select_test_questions(pool)
    assert len(chosen) == 10 and sum(q.level == "A2" for q in chosen) == 4
    assert [q.level for q in chosen] == ["A1"] * 6 + ["A2"] * 4
    small = learning.select_test_questions(pool[:3])
    assert len(small) == 3


# ============================================================================ placement test over HTTP
def test_placement_end_to_end_stores_level_and_shows_result(web, seeded):
    u = mk(placement_done=False)
    csrf = login_as(web, u)

    today = web.get("/today")
    assert today.status_code == 200 and 'href="/placement"' in today.text and 'href="/stuck"' in today.text
    assert web.get("/task").headers["location"] == "/placement"

    first = web.get("/placement")
    assert first.status_code == 200 and "Question 1 of 8" in first.text and 'lang="zh-TW"' in first.text
    no_raw_keys(first.text)

    assert play_test(web, csrf, u.id, "/placement", "/placement/answer") == 8
    result = web.get("/placement")
    assert result.status_code == 200 and 'name="question_id"' not in result.text
    assert "8 / 8" in result.text and "A1" in result.text and "only 8 questions" in result.text

    user = rd(lambda s: s.get(User, u.id))
    # a perfect score still gives A1: only ONE servable A2 item exists in the seed, and one lucky answer is not evidence of A2
    assert user.level == "A1" and user.pretest_done_at is not None
    attempts = fresh_attempts(u.id)
    assert len(attempts) == 8 and all(a.stage == "pretest" and a.is_correct for a in attempts)
    # needs_edit pretest items (Q004, Q009) were never served
    assert {a.question_id for a in attempts}.isdisjoint({"Q004", "Q009"})

    # finished learners see the result again, never a restart, and further answers change nothing
    assert 'name="question_id"' not in web.get("/placement").text
    r = post_answer(web, csrf, "/placement/answer", "Q001", 0, 0)
    assert r.status_code == 303 and len(fresh_attempts(u.id)) == 8
    assert "/today" in web.get("/today").text and 'href="/task"' in web.get("/today").text


def test_all_wrong_answers_place_the_learner_at_a1(web, seeded):
    u = mk(placement_done=False, level="A2")
    csrf = login_as(web, u)
    play_test(web, csrf, u.id, "/placement", "/placement/answer", correct=False)
    user = rd(lambda s: s.get(User, u.id))
    assert user.level == "A1" and user.pretest_done_at is not None
    assert "0 / 8" in web.get("/placement").text


def test_repeat_answer_is_ignored(web, seeded):
    u = mk(placement_done=False)
    csrf = login_as(web, u)
    qid, nonce = page_question(web.get("/placement").text)
    good = choice_for(u.id, qid, nonce, True)
    assert post_answer(web, csrf, "/placement/answer", qid, nonce, good).status_code == 303
    again = post_answer(web, csrf, "/placement/answer", qid, nonce, good + 1 if good < 3 else 0)
    assert again.status_code == 303 and again.headers["location"] == "/placement"
    assert len(fresh_attempts(u.id)) == 1 and fresh_attempts(u.id)[0].is_correct
    assert "already answered" in web.get("/placement").text


@pytest.mark.parametrize("qid,nonce,choice", [
    ("Q999", "0", "0"), ("' OR 1=1 --", "0", "0"), ("Q011", "0", "0"),      # unknown / injected / a daily question
    ("Q004", "0", "0"), ("Q009", "0", "0"),                                   # needs_edit: never part of the test
    ("{q}", "0", "4"), ("{q}", "0", "-1"), ("{q}", "0", "abc"), ("{q}", "0", ""), ("{q}", "0", "99999999999999"),
    ("{q}", "7", "0"), ("{q}", "-1", "0"), ("{q}", "x", "0"),               # forged nonce
])
def test_forged_or_malformed_answers_are_rejected_without_a_500(web, seeded, qid, nonce, choice):
    u = mk(placement_done=False)
    csrf = login_as(web, u)
    real_q, _ = page_question(web.get("/placement").text)
    r = post_answer(web, csrf, "/placement/answer", qid.replace("{q}", real_q), nonce, choice)
    assert r.status_code == 303 and r.headers["location"] == "/placement"
    assert fresh_attempts(u.id) == []
    assert web.get("/placement").status_code == 200  # the flash renders and the test can continue


def test_missing_fields_do_not_crash(web, seeded):
    u = mk(placement_done=False)
    csrf = login_as(web, u)
    r = web.post("/placement/answer", data={"csrf": csrf})
    assert r.status_code == 303 and fresh_attempts(u.id) == []  # empty post while questions remain: nothing is finished
    assert rd(lambda s: s.get(User, u.id)).pretest_done_at is None


def test_placement_with_no_servable_questions_skips_to_a1(web, seeded):
    wr(lambda s: [setattr(q, "active", False) for q in s.scalars(select(Question).where(Question.stage == "pretest"))])
    u = mk(placement_done=False, level="A2")
    csrf = login_as(web, u)
    page = web.get("/placement")
    assert page.status_code == 200 and "level A1" in page.text and 'name="question_id"' not in page.text
    assert web.post("/placement/answer", data={"csrf": csrf}).status_code == 303
    user = rd(lambda s: s.get(User, u.id))
    assert user.level == "A1" and user.pretest_done_at is not None and fresh_attempts(u.id) == []
    assert "started you at level A1" in web.get("/placement").text


def test_another_users_data_is_untouched(web, seeded):
    a, b = mk(placement_done=False), mk()
    # b already has a finished task and some attempts
    csrf_b = login_as(web, b)
    play_task(web, csrf_b, b.id)
    b_attempts = [(x.id, x.question_id, x.is_correct) for x in fresh_attempts(b.id)]
    b_task = today_task(b.id)
    b_answered_qid = b_attempts[0][1]

    csrf_a = login_as(web, a)
    play_test(web, csrf_a, a.id, "/placement", "/placement/answer")
    assert [(x.id, x.question_id, x.is_correct) for x in fresh_attempts(b.id)] == b_attempts
    assert today_task(b.id).status == "done" and rd(lambda s: s.get(User, b.id)).level == "A1"

    # a cannot reach b's answers through the feedback parameter, nor answer b's task questions
    web.get("/task")
    page = web.get(f"/task?feedback={b_answered_qid}")
    assert page.status_code == 200 and "Your answer" not in page.text
    task_a = today_task(a.id)
    assert task_a.id != b_task.id
    r = post_answer(web, csrf_a, "/task/answer", "Q999", 0, 0)
    assert r.status_code == 303 and fresh_attempts(a.id, stage="daily") == []


# ============================================================================ daily task
@pytest.mark.parametrize("minutes,level", [(10, "A1"), (15, "A1"), (20, "A1"), (30, "A1"), (45, "A2")])
def test_task_size_and_new_grammar_follow_the_plan_for_a_fresh_learner(web, seeded, minutes, level):
    u = mk(daily_minutes=minutes, level=level)
    login_as(web, u)
    r = web.get("/task")
    assert r.status_code == 200 and 'name="question_id"' in r.text
    task = today_task(u.id)
    plan = rd(lambda s: s.scalar(select(StudyPlan).where(StudyPlan.user_id == u.id)))
    ids = task.question_ids
    assert len(ids) == len(set(ids)) == plan.exercise_count
    qs = rd(lambda s: list(s.scalars(select(Question).where(Question.id.in_(ids)))))
    assert sum(q.skill == "grammar" for q in qs) == plan.new_grammar
    assert all(q.stage == "daily" and q.review_status != "needs_edit" for q in qs)
    if level == "A1":
        assert all(q.level == "A1" for q in qs)
    assert (task.planned_minutes, task.plan_version, task.status, task.day_no) == (minutes, 1, "pending", 1)


def test_double_request_builds_one_task(web, seeded):
    u = mk()
    login_as(web, u)
    web.get("/task")
    web.get("/task")
    assert rd(lambda s: s.scalar(select(func.count()).select_from(StudyTask).where(StudyTask.user_id == u.id))) == 1


def test_simultaneous_requests_build_one_task_and_store_one_answer(web, seeded):
    """Two browsers tapping at once (double tap / two tabs): the write lock serialises them."""
    import threading
    from fastapi.testclient import TestClient
    u = mk()
    clients = []
    for _ in range(3):
        c = TestClient(web.app, follow_redirects=False)
        login_as(c, u)
        clients.append(c)
    barrier, results = threading.Barrier(3), []

    def hit(c, fn):
        barrier.wait()
        results.append(fn(c).status_code)

    threads = [threading.Thread(target=hit, args=(c, lambda c: c.get("/task"))) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [200, 200, 200]
    assert rd(lambda s: s.scalar(select(func.count()).select_from(StudyTask).where(StudyTask.user_id == u.id))) == 1

    qid, nonce = page_question(clients[0].get("/task").text)
    good = choice_for(u.id, qid, nonce, True)
    csrf = [c.cookies.get("hc_csrf") for c in clients]
    barrier, results = threading.Barrier(3), []
    threads = [threading.Thread(target=hit, args=(c, lambda c, k=k: c.post(
        "/task/answer", data={"csrf": csrf[k], "question_id": qid, "nonce": str(nonce), "choice": str(good)})))
        for k, c in enumerate(clients)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [303, 303, 303] and len(fresh_attempts(u.id)) == 1


def test_insert_task_tolerates_a_task_built_by_a_concurrent_request(session, seeded):
    u = make_user(session)
    day = local_today(u.timezone)
    first = learning._insert_task(session, StudyTask(user_id=u.id, task_date=day, day_no=1, question_ids=["Q011"],
                                                     planned_minutes=30, plan_version=1))
    clash = learning._insert_task(session, StudyTask(user_id=u.id, task_date=day, day_no=2, question_ids=["Q012"],
                                                     planned_minutes=30, plan_version=1))
    assert clash.id == first.id and clash.question_ids == ["Q011"]
    assert session.scalar(select(func.count()).select_from(StudyTask)) == 1


def test_one_question_at_a_time_then_feedback_then_next(web, seeded):
    u = mk(locale="en")
    csrf = login_as(web, u)
    html = web.get("/task").text
    qid, nonce = page_question(html)
    assert "Question 1 of 8" in html and html.count('name="choice"') == 4
    r = post_answer(web, csrf, "/task/answer", qid, nonce, choice_for(u.id, qid, nonce, False))
    assert r.headers["location"] == f"/task?feedback={qid}"
    fb = web.get(r.headers["location"])
    q = rd(lambda s: s.get(Question, qid))
    assert "Not this time" in fb.text and "Right answer" in fb.text and q.options[q.correct_index] in fb.text
    if q.explanation_en or q.explanation_vi or q.explanation_zh:
        assert "Why" in fb.text
    assert "Next question" in fb.text and 'href="/task"' in fb.text
    no_raw_keys(fb.text)
    nxt = web.get("/task")
    assert "Question 2 of 8" in nxt.text and page_question(nxt.text)[0] != qid
    att = fresh_attempts(u.id)[0]
    assert (att.stage, att.task_id, att.is_correct) == ("daily", today_task(u.id).id, False)


def test_answers_outside_the_task_or_twice_are_refused(web, seeded):
    u = mk()
    csrf = login_as(web, u)
    qid, nonce = page_question(web.get("/task").text)
    ids = today_task(u.id).question_ids
    outsider = next(f"Q{n:03d}" for n in range(11, 60) if f"Q{n:03d}" not in ids and f"Q{n:03d}" not in ("Q020", "Q030", "Q032"))
    for bad in (outsider, "Q020", "Q999", "", "<script>"):
        r = post_answer(web, csrf, "/task/answer", bad, 0, 0)
        assert r.status_code == 303 and r.headers["location"] == "/task"
    assert fresh_attempts(u.id) == []
    good = choice_for(u.id, qid, nonce, True)
    post_answer(web, csrf, "/task/answer", qid, nonce, good)
    post_answer(web, csrf, "/task/answer", qid, nonce, good)
    assert len(fresh_attempts(u.id)) == 1
    # forged option index / nonce for the new current question
    qid2, nonce2 = page_question(web.get("/task").text)
    for choice, n in (("9", nonce2), ("-1", nonce2), ("x", nonce2), ("0", nonce2 + 5)):
        post_answer(web, csrf, "/task/answer", qid2, n, choice)
    assert len(fresh_attempts(u.id)) == 1
    # an answer posted before any task exists is just redirected
    other = mk()
    csrf2 = login_as(web, other)
    r = post_answer(web, csrf2, "/task/answer", qid, 0, 0)
    assert r.status_code == 303 and r.headers["location"] == "/task" and fresh_attempts(other.id) == []


def test_task_rebuilt_after_plan_change_only_while_untouched(web, seeded):
    u = mk(daily_minutes=30)
    csrf = login_as(web, u)
    web.get("/task")
    t1 = today_task(u.id)
    assert (len(t1.question_ids), t1.plan_version) == (8, 1)

    def change(minutes, grammar, count, version):
        def go(s):
            user = s.get(User, u.id)
            manual_update(s, user, daily_minutes=minutes, new_grammar=grammar, exercise_count=count, expected_version=version)
        wr(go)

    change(20, 3, 6, 1)                       # untouched task -> rebuilt from the new plan
    html = web.get("/task").text
    t2 = today_task(u.id)
    assert t2.id == t1.id and (len(t2.question_ids), t2.plan_version, t2.planned_minutes) == (6, 2, 20)
    assert "Question 1 of 6" in html

    play_task(web, csrf, u.id, stop_after=1)  # now the learner has started
    change(15, 2, 5, 2)
    html = web.get("/task").text
    t3 = today_task(u.id)
    assert (t3.question_ids, t3.plan_version) == (t2.question_ids, 2)   # kept: answers already exist
    assert "Question 2 of 6" in html
    assert "new plan applies from tomorrow" in web.get("/today").text


def test_resting_learner_gets_no_task(web, seeded):
    u = mk()
    wr(lambda s: coach.pause(s, s.get(User, u.id), 3))
    csrf = login_as(web, u)
    task = web.get("/task")
    assert task.status_code == 200 and "You are resting" in task.text and 'action="/resume"' in task.text
    assert 'name="question_id"' not in task.text
    assert rd(lambda s: s.scalar(select(func.count()).select_from(StudyTask))) == 0
    today = web.get("/today")
    assert "You are resting until" in today.text and 'action="/resume"' in today.text and 'href="/task"' not in today.text
    assert 'href="/stuck"' in today.text  # the way out is always there
    r = post_answer(web, csrf, "/task/answer", "Q011", 0, 0)
    assert r.status_code == 303 and r.headers["location"] == "/today" and fresh_attempts(u.id) == []

    wr(lambda s: setattr(s.get(User, u.id), "paused_until", FAR_FUTURE))
    assert "until you decide to come back" in web.get("/today").text
    wr(lambda s: setattr(s.get(User, u.id), "paused_until", None))   # resting ends: a task appears
    assert 'name="question_id"' in web.get("/task").text


def test_completion_marks_recent_interruptions_resumed(web, seeded):
    u = mk()
    csrf = login_as(web, u)

    def setup(s):
        user = s.get(User, u.id)
        coach.report_interruption(s, user, "too_hard", "hard")
        old = Interruption(user_id=u.id, reason_code="no_time", status="proposed", created_at=utcnow() - timedelta(days=8))
        s.add(old)
        s.flush()
        return old.id
    old_id = wr(setup)

    assert play_task(web, csrf, u.id, stop_after=7) == 7
    assert [i.status for i in rd(lambda s: list(s.scalars(select(Interruption).where(Interruption.reason_code == "too_hard"))))] == ["proposed"]
    assert play_task(web, csrf, u.id) == 1

    task = today_task(u.id)
    assert task.status == "done" and task.completed_at is not None
    rows = {i.reason_code: i for i in rd(lambda s: list(s.scalars(select(Interruption).where(Interruption.user_id == u.id))))}
    assert rows["too_hard"].status == "resumed" and rows["too_hard"].resumed_at is not None
    assert rows["no_time"].id == old_id and rows["no_time"].status == "proposed"  # older than 7 days: untouched
    done = web.get("/task")
    assert "Today's study is finished" in txt(done) and "8 / 8" in done.text and 'name="question_id"' not in done.text
    no_raw_keys(done.text)
    # the last feedback page offers Finish instead of Next
    last_qid = fresh_attempts(u.id)[-1].question_id
    assert "Finish" in web.get(f"/task?feedback={last_qid}").text
    assert "Done for today" in web.get("/today").text


def test_unservable_questions_are_never_shown(web, seeded):
    needs_edit = set(rd(lambda s: list(s.scalars(select(Question.id).where(Question.review_status == "needs_edit")))))
    assert needs_edit  # the seeds do contain flagged items
    for minutes, level in ((30, "A1"), (45, "A2"), (20, "A2"), (10, "A1")):
        u = mk(daily_minutes=minutes, level=level)
        login_as(web, u)
        web.get("/task")
        assert set(today_task(u.id).question_ids).isdisjoint(needs_edit)


def test_pending_review_is_served_only_when_unreviewed_content_is_allowed(web, seeded, monkeypatch):
    monkeypatch.setenv("HUAYU_ALLOW_UNREVIEWED_QUESTIONS", "false")
    get_settings.cache_clear()
    u = mk()
    login_as(web, u)
    page = web.get("/task")                      # every seeded question is still pending_review -> nothing to serve
    assert "No exercises right now" in page.text and today_task(u.id) is None
    assert "no exercises ready" in web.get("/today").text
    assert 'href="/task"' not in web.get("/today").text

    approved = ["Q011", "Q013", "Q015"]
    wr(lambda s: [setattr(s.get(Question, i), "review_status", "approved") for i in approved])
    web.get("/task")
    assert sorted(today_task(u.id).question_ids) == approved and len(today_task(u.id).question_ids) == 3

    monkeypatch.setenv("HUAYU_ALLOW_UNREVIEWED_QUESTIONS", "true")
    get_settings.cache_clear()
    u2 = mk()
    login_as(web, u2)
    web.get("/task")
    assert len(today_task(u2.id).question_ids) == 8
    statuses = {q.review_status for q in rd(lambda s: list(s.scalars(select(Question).where(Question.id.in_(today_task(u2.id).question_ids)))))}
    assert "pending_review" in statuses and statuses <= {"approved", "pending_review"}
    assert {q.review_status for q in rd(lambda s: questions.pool(s, "daily", "A1"))} == {"approved", "pending_review"}


def test_question_flagged_mid_day_is_dropped_from_an_unfinished_task(web, seeded):
    u = mk()
    csrf = login_as(web, u)
    web.get("/task")
    ids = list(today_task(u.id).question_ids)
    play_task(web, csrf, u.id, stop_after=1)
    answered = fresh_attempts(u.id)[0].question_id
    victim = next(i for i in ids if i != answered)
    wr(lambda s: setattr(s.get(Question, victim), "review_status", "needs_edit"))
    html = web.get("/task").text
    assert "Question 2 of 7" in html and victim not in html
    r = post_answer(web, csrf, "/task/answer", victim, 0, 0)
    assert r.status_code == 303 and len(fresh_attempts(u.id)) == 1


def test_history_brings_back_questions_answered_wrong(session, seeded):
    u = onboarded_user(session)
    pool = questions.pool(session, "daily", "A1")
    wrong = pool[0].id
    add_attempt(session, u, wrong, correct=False, skill=pool[0].skill, topic=pool[0].topic, stage="daily",
                when=utcnow() - timedelta(days=2))
    task = learning.get_or_build_task(session, u)
    assert wrong in task.question_ids


def test_service_guards_and_answer_rules(session, seeded):
    u = onboarded_user(session)
    u2 = make_user(session)  # no placement yet
    with pytest.raises(CoachError, match="placement_first"):
        learning.get_or_build_task(session, u2)
    coach.pause(session, u, 2)
    with pytest.raises(CoachError, match="resting"):
        learning.get_or_build_task(session, u)
    coach.resume(session, u)
    task = learning.get_or_build_task(session, u)
    assert learning.get_or_build_task(session, u).id == task.id
    q = session.get(Question, task.question_ids[0])
    with pytest.raises(CoachError, match="invalid_choice"):
        learning.answer_task_question(session, u, task, q.id, 0, 4)
    with pytest.raises(CoachError, match="invalid_choice"):
        learning.answer_task_question(session, u, task, q.id, 0, True)
    with pytest.raises(CoachError, match="stale_question"):
        learning.answer_task_question(session, u, task, q.id, 3, 0)
    with pytest.raises(CoachError, match="not_in_task"):
        learning.answer_task_question(session, u, task, "Q999", 0, 0)
    attempt, finished = learning.answer_task_question(session, u, task, q.id, 0, 0)
    assert not finished and attempt.task_id == task.id
    with pytest.raises(CoachError, match="already_answered"):
        learning.answer_task_question(session, u, task, q.id, 1, 0)
    # a question seen before is shuffled with a new nonce the next time
    assert learning.attempt_nonce(session, u, q.id) == 1


def test_focus_timer_and_sessions_are_shown_only_when_the_plan_has_them(web, seeded):
    u = mk()
    login_as(web, u)
    plain = web.get("/task").text
    assert 'id="focus-timer"' not in plain and "learn.js" not in plain and "shorter sessions" not in plain

    def tweak(s):
        plan = s.scalar(select(StudyPlan).where(StudyPlan.user_id == u.id))
        plan.focus_minutes, plan.sessions_per_day = 25, 2
    wr(tweak)
    html = web.get("/task").text
    assert 'id="focus-timer"' in html and 'data-minutes="25"' in html and 'src="/static/js/learn.js"' in html
    assert "Split into 2 shorter sessions (about 15 minutes each)" in html
    assert "<script>" not in html and "<script " in html and html.count("<script") == 1   # external file only
    today = web.get("/today").text
    assert "Split into 2 shorter sessions" in today and "Focus blocks of 25 minutes" in today
    assert web.get("/static/js/learn.js").status_code == 200 and web.get("/static/css/learn.css").status_code == 200


# ============================================================================ Today page
def test_today_page_states_and_gentle_wording(web, seeded):
    u = mk()
    login_as(web, u)
    html = web.get("/today").text
    for fragment in ("Time: 30 minutes", "Exercises: 8", "New grammar points: up to 5", "Start", 'href="/stuck"', "Your last 7 days",
                     "Nothing yet. That is okay"):
        assert fragment in html
    assert "streak" not in html.lower()
    web.get("/task")
    assert "Start" in web.get("/today").text
    play_task(web, login_as(web, u), u.id, stop_after=3)
    html = web.get("/today").text
    assert "Continue" in html and "3 of 8 done" in html and 'aria-valuenow="3"' in html
    # the mini summary is computed from the learner's own answers
    assert "<b>3</b>Answers" in html


# ============================================================================ post-test
def _pretest_attempts(uid, correct, total, when):
    def go(s):
        for i in range(total):
            qid = f"Q{i + 1:03d}"
            s.add(QuizAttempt(user_id=uid, question_id=qid, stage="pretest", selected_text="x", is_correct=i < correct,
                              skill="vocab", topic=None, created_at=when))
    wr(go)


def test_posttest_is_locked_until_seven_days_after_the_placement_test(web, seeded):
    u = mk()
    csrf = login_as(web, u)
    wr(lambda s: setattr(s.get(User, u.id), "pretest_done_at", utcnow() - timedelta(days=6)))
    opens = learning.posttest_open_date(rd(lambda s: s.get(User, u.id)))
    locked = web.get("/posttest")
    assert locked.status_code == 200 and mon(opens) in locked.text and "Not yet" in locked.text
    assert 'name="question_id"' not in locked.text
    r = post_answer(web, csrf, "/posttest/answer", "Q101", 0, 0)
    assert r.status_code == 303 and r.headers["location"] == "/posttest" and fresh_attempts(u.id) == []
    assert "not open yet" in web.get("/posttest").text
    assert "self-check is ready" not in web.get("/today").text

    wr(lambda s: setattr(s.get(User, u.id), "pretest_done_at", utcnow() - timedelta(days=7)))
    assert 'name="question_id"' in web.get("/posttest").text
    assert "self-check is ready" in web.get("/today").text


def test_posttest_end_to_end_shows_pre_and_post_side_by_side(web, seeded):
    u = mk()
    csrf = login_as(web, u)
    wr(lambda s: setattr(s.get(User, u.id), "pretest_done_at", utcnow() - timedelta(days=9)))
    _pretest_attempts(u.id, 5, 8, utcnow() - timedelta(days=9))
    assert 'name="question_id"' in web.get("/posttest").text
    n = play_test(web, csrf, u.id, "/posttest", "/posttest/answer")
    assert n == 9   # seeded post-test items that may be served (the needs_edit one is excluded)
    page = web.get("/posttest")
    assert "At the start" in page.text and "5 / 8" in page.text and "Now" in page.text and "9 / 9" in page.text
    assert "not an official result" in page.text
    no_raw_keys(page.text)
    assert rd(lambda s: s.get(User, u.id)).posttest_done_at is not None
    assert all(a.stage == "posttest" for a in fresh_attempts(u.id, stage="posttest"))
    # finished: no restart, repeated posts change nothing
    post_answer(web, csrf, "/posttest/answer", "Q101", 0, 0)
    assert len(fresh_attempts(u.id, stage="posttest")) == 9
    progress = web.get("/progress").text
    assert "5 of 8 right" in progress and "9 of 9 right" in progress


def test_posttest_requires_the_placement_test_first(web, seeded):
    u = mk(placement_done=False)
    login_as(web, u)
    assert web.get("/posttest").headers["location"] == "/placement"


# ============================================================================ progress report
def test_progress_numbers_match_the_database_and_percentages_need_five_answers(session, seeded):
    u = onboarded_user(session)
    now = utcnow()
    for i in range(5):
        add_attempt(session, u, f"P{i}", correct=i < 3, skill="grammar", when=now - timedelta(hours=i))
    for i in range(4):
        add_attempt(session, u, f"V{i}", correct=i < 2, skill="vocab", when=now - timedelta(hours=i))
    add_attempt(session, u, "OLD", correct=True, skill="reading", when=now - timedelta(days=8))   # outside the window
    other = make_user(session)
    add_attempt(session, other, "O1", correct=True, skill="reading")                                # somebody else
    rep = learning.progress_report(session, u, now)
    skills = {s.skill: s for s in rep.skills}
    assert (skills["grammar"].answers, skills["grammar"].correct, skills["grammar"].percent) == (5, 3, 60)
    assert (skills["vocab"].answers, skills["vocab"].correct, skills["vocab"].percent) == (4, 2, None)
    assert (skills["reading"].answers, skills["reading"].percent) == (0, None)
    assert rep.total_answers == 9


def test_progress_window_edges_are_whole_local_days(session):
    u = onboarded_user(session)
    now = utcnow()
    start = local_midnight_utc(local_today(u.timezone, now) - timedelta(days=6), u.timezone)
    add_attempt(session, u, "IN", correct=True, when=start)
    add_attempt(session, u, "OUT", correct=True, when=start - timedelta(seconds=1))
    assert learning.progress_report(session, u, now).total_answers == 1


def test_progress_tasks_interruptions_and_plan_changes(session):
    u = onboarded_user(session)
    now = utcnow()
    today = local_today(u.timezone, now)

    def task(day_offset, status):
        session.add(StudyTask(user_id=u.id, task_date=today - timedelta(days=day_offset), day_no=1, question_ids=[],
                              planned_minutes=30, plan_version=1, status=status))
    task(0, "pending")     # today, still in progress: not counted as a miss
    task(1, "done")
    task(2, "pending")
    task(3, "done")
    task(9, "done")        # outside the window
    for reason, status, source, age in (("too_hard", "resumed", "actual", 1), ("too_hard", "proposed", "actual", 2),
                                        ("need_rest", "accepted", "actual", 3), ("no_time", "resumed", "simulated", 1),
                                        ("distracted", "resumed", "actual", 12)):
        session.add(Interruption(user_id=u.id, reason_code=reason, status=status, source=source,
                                 created_at=now - timedelta(days=age)))
    session.add_all([
        PlanChange(user_id=u.id, source="learner", result="manual", old_values={"daily_minutes": 30, "exercise_count": 8},
                   new_values={"daily_minutes": 20, "exercise_count": 8}, old_version=1, new_version=2,
                   created_at=now - timedelta(days=4)),
        PlanChange(user_id=u.id, source="proposal", result="partial", old_values={"exercise_count": 8, "focus_minutes": None},
                   new_values={"exercise_count": 5, "focus_minutes": 25}, old_version=2, new_version=3,
                   created_at=now - timedelta(days=1)),
        PlanChange(user_id=u.id, source="proposal", result="reject", old_values={}, new_values={}, old_version=3,
                   new_version=3, created_at=now),
        PlanChange(user_id=u.id, source="proposal", result="accept", old_values={"new_grammar": 5}, new_values={"new_grammar": 2},
                   old_version=0, new_version=1, created_at=now - timedelta(days=20)),
    ])
    session.flush()
    rep = learning.progress_report(session, u, now)
    assert (rep.tasks_done, rep.tasks_total) == (2, 3)
    assert [(r.reason, r.count, r.resumed) for r in rep.reasons] == [("too_hard", 2, 1), ("need_rest", 1, 0)]
    assert (rep.interruptions, rep.resumed) == (3, 1)
    assert rep.plan_changes == 2
    assert rep.last_change.source == "proposal"
    assert [(c["field"], c["old"], c["new"]) for c in rep.last_change.changes] == [
        ("exercise_count", 8, 5), ("focus_minutes", None, 25)]


def test_progress_page_renders_the_numbers_and_hides_small_percentages(web, seeded):
    u = mk()
    csrf = login_as(web, u)
    wr(lambda s: [add_attempt(s, s.get(User, u.id), f"P{i}", correct=i < 3, skill="grammar") for i in range(5)])
    wr(lambda s: [add_attempt(s, s.get(User, u.id), f"V{i}", correct=i < 2, skill="vocab") for i in range(4)])
    wr(lambda s: s.add(Interruption(user_id=u.id, reason_code="no_time", status="resumed", created_at=utcnow())))
    html = web.get("/progress").text
    assert "3 of 5 right (60%)" in html
    assert "Answers so far: 4" in html and "50%" not in html
    assert "No answers yet" in html           # reading
    assert "Told us: 1." in html and "No time" in html
    assert "Studied again" not in html, "no return-rate ledger of the learner's own distress reports"
    assert "No plan changes in the last 7 days." in html
    assert "No daily task opened" in html
    no_raw_keys(html)
    # a learner with nothing recorded still gets a calm page
    empty = mk()
    login_as(web, empty)
    page = web.get("/progress")
    assert page.status_code == 200 and "%" not in page.text.replace("width: 0%", "")


# ============================================================================ access control, CSRF, i18n
ALL_GET = ["/today", "/placement", "/task", "/posttest", "/progress"]
ALL_POST = ["/placement/answer", "/task/answer", "/posttest/answer"]


@pytest.mark.parametrize("path", ALL_GET)
def test_anonymous_visitors_are_sent_to_login(web, path):
    r = web.get(path)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", ALL_POST)
def test_anonymous_posts_are_sent_to_login(web, path):
    from app.security import new_csrf_token
    data = {"question_id": "Q001", "nonce": "0", "choice": "0"}
    assert web.post(path, data=data).status_code == 403          # no token at all: refused outright
    token = new_csrf_token()
    web.cookies.set("hc_csrf", token)                            # e.g. the session expired but the page was still open
    r = web.post(path, data={**data, "csrf": token})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", ALL_GET)
def test_learners_who_have_not_onboarded_are_sent_to_onboarding(web, path):
    u = mk(onboarded=False)
    login_as(web, u)
    r = web.get(path)
    assert r.status_code == 303 and r.headers["location"] == "/onboarding"


@pytest.mark.parametrize("path", ALL_POST)
def test_every_post_needs_a_csrf_token(web, seeded, path):
    u = mk()
    csrf = login_as(web, u)
    data = {"question_id": "Q001", "nonce": "0", "choice": "0"}
    assert web.post(path, data=data).status_code == 403                                # missing
    assert web.post(path, data={**data, "csrf": "forged"}).status_code == 403          # wrong
    assert web.post(path, data={**data, "csrf": csrf + "x"}).status_code == 403        # tampered
    assert web.post(path, data=data, headers={"x-csrf-token": "nope"}).status_code == 403
    assert fresh_attempts(u.id) == []
    assert web.post(path, data={**data, "csrf": csrf}).status_code == 303              # a valid token gets through


@pytest.mark.parametrize("loc", LOCALES)
def test_no_raw_translation_keys_in_any_locale(web, seeded, loc):
    def visit(path, expect=200):
        r = web.get(path)
        assert r.status_code == expect, (path, r.status_code)
        no_raw_keys(r.text)
        assert f'lang="{loc}"' in r.text
        return r

    # 1. brand-new learner: Today -> placement questions -> result -> progress
    a = mk(locale=loc, placement_done=False)
    csrf = login_as(web, a)
    visit("/today")
    visit("/placement")
    play_test(web, csrf, a.id, "/placement", "/placement/answer")
    visit("/placement")
    visit("/today")
    visit("/progress")
    visit("/posttest")                                    # locked page (+ formatted date)

    # 2. a task through to the completion screen, with sessions and a focus block
    def tweak(s):
        plan = s.scalar(select(StudyPlan).where(StudyPlan.user_id == a.id))
        plan.focus_minutes, plan.sessions_per_day = 25, 2
    wr(tweak)
    visit("/task")
    qid, nonce = page_question(web.get("/task").text)
    r = post_answer(web, csrf, "/task/answer", qid, nonce, choice_for(a.id, qid, nonce, False))
    visit(r.headers["location"])
    visit("/today")
    play_task(web, csrf, a.id)
    visit("/task")
    visit("/today")
    post_answer(web, csrf, "/task/answer", "Q999", 0, 0)   # an error flash renders translated
    visit("/today")

    # 3. resting
    wr(lambda s: coach.pause(s, s.get(User, a.id), 2))
    visit("/today")
    visit("/task")
    wr(lambda s: setattr(s.get(User, a.id), "paused_until", FAR_FUTURE))
    visit("/today")
    wr(lambda s: setattr(s.get(User, a.id), "paused_until", None))

    # 4. post-test: question page, result, progress with both scores
    wr(lambda s: setattr(s.get(User, a.id), "pretest_done_at", utcnow() - timedelta(days=8)))
    visit("/today")
    visit("/posttest")
    play_test(web, csrf, a.id, "/posttest", "/posttest/answer")
    visit("/posttest")
    visit("/progress")

    # 5. nothing to serve
    wr(lambda s: [setattr(q, "active", False) for q in s.scalars(select(Question).where(Question.stage != "pretest"))])
    b = mk(locale=loc, placement_done=False)
    csrf_b = login_as(web, b)
    wr(lambda s: s.get(User, b.id).__setattr__("pretest_done_at", None))
    wr(lambda s: [setattr(q, "active", False) for q in s.scalars(select(Question).where(Question.stage == "pretest"))])
    visit("/placement")                                   # "no questions" page
    web.post("/placement/answer", data={"csrf": csrf_b})
    visit("/placement")
    visit("/task")                                        # nothing to serve
    visit("/today")


def test_every_translation_key_used_by_a_template_exists_in_all_locales():
    keys = set()
    for path in Path("app/templates/learn").glob("*.html"):
        keys |= set(re.findall(r"""\bt\(\s*'(learn\.[\w.]+)'\s*[,)]""", path.read_text(encoding="utf-8")))
    assert len(keys) > 60
    for loc in LOCALES:
        missing = sorted(k for k in keys if k not in i18n.catalog(loc))
        assert not missing, (loc, missing)
    # flash keys the router can produce
    from app.routers.learn import ERROR_CODES
    for loc in LOCALES:
        for code in (*ERROR_CODES, "generic"):
            assert f"learn.err.{code}" in i18n.catalog(loc), (loc, code)


def test_templates_have_no_inline_script_or_unsafe_markup():
    for path in Path("app/templates/learn").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "|safe" not in text and "<script>" not in text and "onclick" not in text, path.name


# ============================================================================ honesty review: what the learner screens may show
CONTROL_TEXT = re.compile(r"<button class=\"option\".*?</button>", re.S)


def text_nodes_outside_options(html: str) -> list[str]:
    """Visible text of <main> that is not an answer button (scripts and styles dropped)."""
    body = html.split("<main", 1)[1].split("</main>", 1)[0]
    body = re.sub(r"<(script|style)\b.*?</\1>", "", CONTROL_TEXT.sub("", body), flags=re.S)
    return [htmllib.unescape(t).strip() for t in re.findall(r">([^<>]+)<", body) if t.strip()]


def assert_gives_nothing_away(html: str, qid: str) -> None:
    q = rd(lambda s: s.get(Question, qid))
    right = q.options[q.correct_index]
    nodes = [n for n in text_nodes_outside_options(html) if n != q.question_zh]
    assert 'class="zh-sm"' not in html, qid
    for hint in filter(None, (q.topic, right)):
        assert not any(hint in n or n in hint and len(n) > 1 for n in nodes), (qid, hint, nodes)


def test_no_question_page_shows_the_grammar_topic_before_the_learner_answers(web, seeded):
    """F02: for 8 daily questions the grammar topic WAS the answer (有 / 是 / 在 / 去 / 很 / 完), printed above the stem."""
    served = {"daily": [], "pretest": [], "posttest": []}

    # every servable daily question, through the real /task page (a plan big enough to hold the whole A2 pool)
    u = mk(level="A2")
    def big_plan(s):
        plan = s.scalar(select(StudyPlan).where(StudyPlan.user_id == u.id))
        plan.exercise_count, plan.new_grammar = 40, 10
    wr(big_plan)
    csrf = login_as(web, u)
    for _ in range(60):
        html = web.get("/task").text
        if 'name="question_id"' not in html:
            break
        qid, nonce = page_question(html)
        assert_gives_nothing_away(html, qid)
        served["daily"].append(qid)
        post_answer(web, csrf, "/task/answer", qid, nonce, choice_for(u.id, qid, nonce, True))
    pool = rd(lambda s: [q.id for q in questions.pool(s, "daily", "A2")])
    assert sorted(served["daily"]) == sorted(pool), "the test must have seen every servable daily question"

    # placement test and post-test pages
    for stage, path, answer in (("pretest", "/placement", "/placement/answer"), ("posttest", "/posttest", "/posttest/answer")):
        v = mk(level="A2", placement_done=False)
        csrf = login_as(web, v)
        if stage == "posttest":
            wr(lambda s: setattr(s.get(User, v.id), "pretest_done_at", utcnow() - timedelta(days=9)))
        for _ in range(40):
            html = web.get(path).text
            if 'name="question_id"' not in html:
                break
            qid, nonce = page_question(html)
            assert_gives_nothing_away(html, qid)
            served[stage].append(qid)
            post_answer(web, csrf, answer, qid, nonce, choice_for(v.id, qid, nonce, True))
        assert served[stage], stage

    # the seed really does contain topics that equal (or sit inside) the answer: the check above would have caught them
    leaky = rd(lambda s: [q.id for q in questions.pool(s, "daily", "A2")
                          if q.topic and (q.topic in q.options[q.correct_index] or q.options[q.correct_index] in q.topic)])
    assert leaky and set(leaky) <= set(served["daily"])


def test_a_question_the_learner_has_answered_before_says_so_without_blame(web, seeded):
    """F01: when the small bank forces repeats the page says why, in plain neutral words."""
    u = mk(level="A1")
    csrf = login_as(web, u)
    first = web.get("/task").text
    assert 'data-role="seen-before"' not in first and "answered this question before" not in first
    pool = rd(lambda s: [q.id for q in questions.pool(s, "daily", "A1")])
    def seen_everything(s):
        for qid in pool:
            add_attempt(s, s.get(User, u.id), qid, correct=True, skill="grammar")
    wr(seen_everything)
    wr(lambda s: [s.delete(t) for t in s.scalars(select(StudyTask).where(StudyTask.user_id == u.id))])
    again = web.get("/task").text
    assert 'data-role="seen-before"' in again and "answered this question before" in again
    assert "question bank is still small" in again and "streak" not in again.lower() and "missed" not in again.lower()


@pytest.mark.parametrize("loc,fields,label,lang", [
    ("en", {"explanation_vi": "你好 = xin chào."}, "Explanation in Vietnamese", "vi"),
    ("en", {"explanation_vi": "「你好」是打招呼。"}, "Explanation in Chinese", "zh-TW"),   # Q002-style: the Vietnamese field holds Chinese
    ("zh-TW", {"explanation_vi": "你好 = xin chào."}, "說明文字為越南語", "vi"),
    ("vi", {"explanation_zh": "「你好」是打招呼。", "explanation_vi": None}, "Giải thích bằng tiếng Hoa", "zh-TW"),
    ("vi", {"explanation_en": "Hello.", "explanation_vi": None, "explanation_zh": None}, "Giải thích bằng tiếng Anh", "en"),
    ("en", {"explanation_en": "Hello, said politely."}, None, None),        # the learner's own language: no label
    ("vi", {"explanation_vi": "Lời chào lịch sự."}, None, None),
    ("zh-TW", {"explanation_zh": "打招呼的用語。"}, None, None),
])
def test_an_explanation_in_another_language_is_labelled_and_marked_up(web, seeded, loc, fields, label, lang):
    """F03: the fallback language is named instead of silently mixing languages."""
    u = mk(locale=loc)
    csrf = login_as(web, u)
    qid, nonce = page_question(web.get("/task").text)
    blank = {"explanation_vi": None, "explanation_zh": None, "explanation_en": None}
    wr(lambda s: [setattr(s.get(Question, qid), k, v) for k, v in {**blank, **fields}.items()])
    r = post_answer(web, csrf, "/task/answer", qid, nonce, choice_for(u.id, qid, nonce, False))
    html = web.get(r.headers["location"]).text
    text = next(v for v in fields.values() if v)
    assert htmllib.escape(text, quote=False) in html or htmllib.escape(text) in html
    if label is None:
        assert 'class="lang-note"' not in html
    else:
        assert f'<p class="lang-note">{label}</p>' in html and f'<p lang="{lang}">' in html


def test_non_chinese_answer_text_is_marked_with_its_language(web, seeded):
    """F17(c): the Vietnamese gloss options carry lang="vi" on an English page, and not on a Vietnamese page."""
    en = mk(locale="en", placement_done=False)
    login_as(web, en)
    html = web.get("/placement").text
    options = CONTROL_TEXT.findall(html)
    assert options and any('<span lang="vi">' in o for o in options)
    vi = mk(locale="vi", placement_done=False)
    login_as(web, vi)
    assert '<span lang="vi">' not in web.get("/placement").text


def test_today_hides_zero_tiles_and_the_progress_page_has_no_scoreboard(web, seeded):
    """F18: no '0 tasks finished' tile, no x / y denominators, no return-rate ledger."""
    u = mk()
    csrf = login_as(web, u)
    play_task(web, csrf, u.id, correct=False, stop_after=2)
    html = web.get("/today").text
    assert "<b>2</b>Answers" in html and "Tasks finished" not in html and "<b>0</b>" not in html and ">Right<" not in html
    progress = web.get("/progress").text
    assert "Studied again" not in progress and re.search(r"<b>\d+ / \d+</b>", progress) is None
    play_task(web, csrf, u.id, correct=True)
    done = web.get("/today").text
    assert "Tasks finished" in done                       # shown once there is something to show
    assert re.search(r"<b>1</b>daily tasks finished", web.get("/progress").text)


@pytest.mark.parametrize("loc", LOCALES)
def test_placement_copy_promises_no_question_count(loc):
    """F08: the check has as many questions as the bank allows (8 today); the result page prints the real number."""
    assert not re.search(r"\d", i18n.t(loc, "learn.today.placement_body")), i18n.t(loc, "learn.today.placement_body")
    assert i18n.t("en", "learn.placement.level_A2").startswith("We will start you at A2")  # a starting point, not a diagnosis


def test_resting_learner_page_has_exactly_one_h1_and_no_dangling_aria_references(web, seeded):
    u = mk()
    login_as(web, u)
    wr(lambda s: coach.pause(s, s.get(User, u.id), 2))
    for path in ("/task", "/today"):
        html = web.get(path).text
        assert html.count("<h1") == 1, path
    wr(lambda s: setattr(s.get(User, u.id), "paused_until", None))
    pages = [web.get(p).text for p in ("/today", "/progress", "/task")]
    for html in pages:
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        for attr in ("aria-labelledby", "aria-describedby"):
            for ref in re.findall(attr + r'="([^"]+)"', html):
                assert set(ref.split()) <= ids, (attr, ref)
        assert html.count("<h1") == 1


def test_focus_timer_is_readable_and_announces_its_end_without_aria_hidden(web, seeded):
    u = mk()
    login_as(web, u)
    def tweak(s):
        plan = s.scalar(select(StudyPlan).where(StudyPlan.user_id == u.id))
        plan.focus_minutes = 25
    wr(tweak)
    html = web.get("/task").text
    assert 'role="timer"' in html and 'aria-hidden="true">25:00' not in html
    assert 'data-role="announce" role="status"' in html            # a live region that is in the page from the start
    js = web.get("/static/js/learn.js").text
    assert "announce.textContent" in js


# ============================================================================ post-test date maths
def test_posttest_opens_seven_local_days_after_the_placement_test(session):
    u = onboarded_user(session)
    u.pretest_done_at = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)   # 02:00 on Nov 2 in Taipei
    assert learning.posttest_open_date(u) == date(2026, 11, 9)
    assert not learning.posttest_available(u, datetime(2026, 11, 8, 15, 59, tzinfo=timezone.utc))   # still Nov 8 locally
    assert learning.posttest_available(u, datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc))        # Nov 9 00:00 locally
    u.pretest_done_at = None
    assert learning.posttest_open_date(u) is None and not learning.posttest_available(u)
