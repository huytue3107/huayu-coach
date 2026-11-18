"""Slice D: staff area (dashboard, question bank, users, plans/payments, audit). Only the admin + billing_web routers are mounted."""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES, get_settings
from app.routers import admin as admin_mod
from app.services import admin_stats, billing
from app.services.seed import seed_all
from app.timeutil import utcnow
from tests.conftest import add_attempt, login_as, make_user

SEEDS = Path(__file__).resolve().parent.parent / "seeds" / "questions.json"


@pytest.fixture(autouse=True)
def _only_my_routers(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "admin,billing_web")


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("HUAYU_BILLING_ENABLED", "true")
    get_settings.cache_clear()


# ----------------------------------------------------------------------------- helpers
def new_user(role="learner", locale="en", **kw):
    """A committed user. The saved language beats the Accept-Language header, so pages render in English unless told otherwise."""
    with dbmod.write_session() as s:
        user = make_user(s, role=role, **kw)
        user.locale = locale
        return user


def as_user(web, user):
    web.cookies.clear()
    return login_as(web, user)


def read(fn):
    """Run `fn(session)` in a short read session; returned ORM objects stay readable (detached, attributes loaded)."""
    with dbmod.read_session() as s:
        result = fn(s)
        s.expunge_all()
        return result


def count(model, *where):
    return read(lambda s: s.scalar(select(func.count()).select_from(model).where(*where)))


def audit_actions():
    return read(lambda s: list(s.scalars(select(models.AuditLog.action).order_by(models.AuditLog.id))))


def question_form(**over):
    data = {"question_zh": "我喜歡喝茶。", "option_0": "uống", "option_1": "ăn", "option_2": "mua", "option_3": "bán",
            "correct": "0", "topic": "喝", "level": "A1", "skill": "vocab", "stage": "daily",
            "explanation_vi": "喝 = uống", "explanation_zh": "", "explanation_en": "", "review_note": ""}
    data.update(over)
    return data


def post_q(web, token, path, **over):
    return web.post(path, data={"csrf": token, **question_form(**over)})


VOID = {"meta", "link", "input", "br", "hr", "img", "source", "area", "col", "base", "wbr"}


def assert_balanced(html: str, where: str = "") -> None:
    """Every opening tag is closed in the right order (catches template typos)."""
    from html.parser import HTMLParser
    stack: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                stack.append(tag)

        def handle_endtag(self, tag):
            assert stack and stack[-1] == tag, f"{where}: </{tag}> closes <{stack[-1] if stack else None}>"
            stack.pop()

    P().feed(html)
    assert not stack, f"{where}: unclosed {stack}"


# ============================================================================ role gating
READ_PAGES = {  # path -> roles that may open it
    "/admin": ("teacher", "admin"), "/admin/questions": ("teacher", "admin"), "/admin/questions/new": ("teacher", "admin"),
    "/admin/questions/Q001": ("teacher", "admin"), "/admin/questions/export": ("teacher", "admin"),
    "/admin/users": ("admin",), "/admin/users/1": ("admin",), "/admin/billing": ("admin",), "/admin/audit": ("admin",),
}


@pytest.mark.parametrize("path", sorted(READ_PAGES))
def test_role_gating_on_every_page(web, seeded, path):
    users = {role: new_user(role) for role in ("learner", "teacher", "admin")}
    web.cookies.clear()
    r = web.get(path)
    assert r.status_code == 303 and r.headers["location"].startswith("/login"), "anonymous is sent to the login page"
    for role, user in users.items():
        as_user(web, user)
        want = 200 if role in READ_PAGES[path] else 403
        assert web.get(path).status_code == want, (path, role)


def test_unknown_ids_are_404_never_500(web, seeded):
    admin = new_user("admin")
    token = as_user(web, admin)
    for path in ("/admin/questions/NOPE", "/admin/users/99999", f"/admin/users/{10**30}", "/admin/users/abc"):
        assert web.get(path).status_code == 404, path
    assert web.post("/admin/questions/NOPE/review", data={"csrf": token, "status": "approved"}).status_code == 404
    assert web.post(f"/admin/users/{10**30}/grant", data={"csrf": token, "days": "3"}).status_code == 404
    assert web.post("/admin/billing/plans/nope", data={"csrf": token}).status_code == 404


# ============================================================================ csrf + role on every POST
def post_targets(learner_id):
    qform = question_form()
    plan_form = {"name_vi": "a", "name_zh": "b", "name_en": "c", "price_twd": "10", "period_days": "5"}
    return [  # (path, extra form fields, roles allowed)
        ("/admin/questions/new", qform, ("teacher", "admin")),
        ("/admin/questions/Q001", qform, ("teacher", "admin")),
        ("/admin/questions/Q001/review", {"status": "approved"}, ("teacher", "admin")),
        ("/admin/questions/import", {}, ("teacher", "admin")),
        (f"/admin/users/{learner_id}/grant", {"days": "3"}, ("admin",)),
        (f"/admin/users/{learner_id}/activate", {"plan_code": "monthly"}, ("admin",)),
        (f"/admin/users/{learner_id}/role", {"role": "teacher"}, ("admin",)),
        (f"/admin/users/{learner_id}/sessions", {}, ("admin",)),
        ("/admin/billing/plans", {"code": "yearly", **plan_form}, ("admin",)),
        ("/admin/billing/plans/monthly", plan_form, ("admin",)),
    ]


def test_every_post_needs_csrf_and_the_right_role(web, seeded):
    learner = new_user("learner")
    users = {role: new_user(role) for role in ("learner", "teacher", "admin")}
    before = (count(models.AuditLog), count(models.Question), count(models.Subscription), count(models.BillingPlan))
    for path, form, roles in post_targets(learner.id):
        files = {"file": ("q.json", b"[]", "application/json")} if path.endswith("/import") else None
        web.cookies.clear()
        r = web.post(path, data=form, files=files)  # anonymous, no csrf
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
        for role, user in users.items():
            as_user(web, user)
            r = web.post(path, data=form, files=files)  # logged in, but no csrf token
            assert r.status_code == 403, (path, role, "csrf")
            token = login_as(web, user)
            web.cookies.set("hc_csrf", token)
            r = web.post(path, data={**form, "csrf": "forged"}, files=files)
            assert r.status_code == 403, (path, role, "forged csrf")
            if role not in roles:
                r = web.post(path, data={**form, "csrf": token}, files=files)
                assert r.status_code == 403, (path, role, "role")
    assert before == (count(models.AuditLog), count(models.Question), count(models.Subscription), count(models.BillingPlan))


# ============================================================================ commit before the redirect leaves the server
def rows_visible_when_response_is_sent(web, path: str, form: dict) -> tuple[int, int]:
    """(audit rows before the request, audit rows another connection can see at the moment the response body is sent)."""
    import asyncio
    from urllib.parse import urlencode
    body = urlencode(form).encode()
    cookie = "; ".join(f"{k}={v}" for k, v in web.cookies.items())
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": path, "raw_path": path.encode(),
             "query_string": b"", "scheme": "http", "root_path": "", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"host", b"test"), (b"content-type", b"application/x-www-form-urlencoded"),
                         (b"content-length", str(len(body)).encode()), (b"cookie", cookie.encode())]}
    seen: dict[str, int] = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
        if message["type"] == "http.response.body" and not message.get("more_body"):
            seen["visible"] = count(models.AuditLog)

    before = count(models.AuditLog)
    asyncio.run(web.app(scope, receive, send))
    assert seen["status"] == 303, (path, seen)
    return before, seen["visible"]


def test_changes_are_committed_before_the_redirect_is_sent(web, seeded, billing_on):
    """FastAPI runs get_wdb's commit AFTER the response is sent; a browser following the redirect could read stale data.
    The handlers commit themselves, so the page the learner lands on already shows the change."""
    admin, learner = new_user("admin"), new_user("learner")
    with dbmod.write_session() as s:
        billing.seed_plans(s)
    token = as_user(web, admin)
    cases = [("/admin/questions/Q001/review", {"status": "approved"}),
             ("/admin/questions/new", question_form()),
             ("/admin/questions/Q002", question_form(question_zh="changed")),
             (f"/admin/users/{learner.id}/grant", {"days": "3"}),
             (f"/admin/users/{learner.id}/activate", {"plan_code": "monthly"}),
             (f"/admin/users/{learner.id}/role", {"role": "teacher"}),
             (f"/admin/users/{learner.id}/sessions", {}),
             ("/admin/billing/plans/monthly", plan_data()),
             ("/admin/billing/plans", {"code": "yearly", **plan_data()})]
    for path, form in cases:
        before, visible = rows_visible_when_response_is_sent(web, path, {"csrf": token, **form})
        assert visible == before + 1, path


# ============================================================================ question rules (unit)
GOOD = {"question_zh": "你好", "options": ["a", "b", "c", "d"], "correct_index": 1, "topic": None, "level": "A1",
        "skill": "vocab", "stage": "daily"}


@pytest.mark.parametrize("change,field,code", [
    ({"question_zh": "  "}, "question_zh", "required"),
    ({"question_zh": "x" * 501}, "question_zh", "too_long"),
    ({"question_zh": 5}, "question_zh", "invalid_type"),
    ({"options": ["a", "b", "c"]}, "options", "options_count"),
    ({"options": ["a", "b", "c", "d", "e"]}, "options", "options_count"),
    ({"options": "abcd"}, "options", "options_count"),
    ({"options": ["a", "b", "c", " "]}, "options", "options_empty"),
    ({"options": ["a", "b", "c", "a"]}, "options", "options_distinct"),
    ({"options": ["a", "b", "c", " A "]}, "options", "options_distinct"),
    ({"options": ["a", "b", "c", "d" * 201]}, "options", "too_long"),
    ({"options": ["a", "b", 3, "d"]}, "options", "invalid_type"),
    ({"correct_index": 4}, "correct_index", "correct_index"),
    ({"correct_index": -1}, "correct_index", "correct_index"),
    ({"correct_index": True}, "correct_index", "correct_index"),
    ({"correct_index": "x"}, "correct_index", "correct_index"),
    ({"level": "B1"}, "level", "invalid_choice"),
    ({"skill": "listening"}, "skill", "invalid_choice"),
    ({"stage": "exam"}, "stage", "invalid_choice"),
    ({"topic": "t" * 31}, "topic", "too_long"),
    ({"explanation_en": "e" * 1001}, "explanation_en", "too_long"),
    ({"day": 400}, "day", "invalid_number"),
    ({"review_status": "bogus"}, "review_status", "invalid_choice"),
])
def test_validate_question_rejects(change, field, code):
    clean, errors = admin_mod.validate_question({**GOOD, **change})
    assert errors == {field: code}


def test_validate_question_accepts_and_cleans():
    clean, errors = admin_mod.validate_question({**GOOD, "correct_index": "3", "question_zh": " 你\x00好 ", "options": [" a ", "b", "c", "d"]})
    assert errors == {} and clean["correct_index"] == 3 and clean["question_zh"] == "你好" and clean["options"][0] == "a"
    assert clean["explanation_vi"] is None and clean["topic"] is None


def test_every_error_code_and_field_has_a_translation():
    codes = {"required", "too_long", "invalid_type", "options_count", "options_empty", "options_distinct", "correct_index",
             "invalid_choice", "invalid_number", "bad_id", "not_object"}
    fields = {"question_zh", "options", "correct_index", "topic", "stage", "level", "skill", "explanation_vi", "explanation_zh",
              "explanation_en", "review_note", "day", "source", "review_status", "id", "item"}
    for loc in LOCALES:
        for c in codes:
            assert i18n.t(loc, f"admin.q.err.{c}") != f"admin.q.err.{c}"
        for f in fields:
            assert i18n.t(loc, f"admin.q.field.{f}") != f"admin.q.field.{f}"


def test_next_question_id(session):
    assert admin_mod.next_question_id(session) == "Q001"
    for qid in ("Q007", "Q050", "X9", "Qabc"):
        session.add(models.Question(id=qid, stage="daily", level="A1", skill="vocab", question_zh="q", options=list("abcd"), correct_index=0))
    session.flush()
    assert admin_mod.next_question_id(session) == "Q051"


# ============================================================================ question pages
def listed(response) -> list[str]:
    """Question ids shown in the table, in order."""
    return re.findall(r'href="/admin/questions/(Q[A-Za-z0-9_-]*)"', response.text)


def test_list_filters_search_and_pagination(web, seeded):
    as_user(web, new_user("teacher"))
    all_ids = read(lambda s: list(s.scalars(select(models.Question.id).order_by(models.Question.id))))
    page1, page2 = web.get("/admin/questions"), web.get("/admin/questions?page=2")
    assert listed(page1) == all_ids[:25] and listed(page2) == all_ids[25:] and "Page 1 of 2" in page1.text and "50 questions" in page1.text
    assert listed(web.get("/admin/questions?page=banana")) == all_ids[:25] and listed(web.get("/admin/questions?page=999")) == all_ids[25:]

    def ids_where(*cond):
        return read(lambda s: list(s.scalars(select(models.Question.id).where(*cond).order_by(models.Question.id))))

    for stage in ("pretest", "daily", "posttest"):
        assert listed(web.get(f"/admin/questions?stage={stage}")) == ids_where(models.Question.stage == stage)[:25]
    for skill in ("vocab", "grammar"):
        assert listed(web.get(f"/admin/questions?skill={skill}&level=A1")) == ids_where(
            models.Question.skill == skill, models.Question.level == "A1")[:25]
    topic = read(lambda s: s.scalars(select(models.Question.topic).where(models.Question.topic.is_not(None))).first())
    assert listed(web.get(f"/admin/questions?topic={topic}")) == ids_where(models.Question.topic == topic)
    assert listed(web.get("/admin/questions?stage=pretest&skill=grammar&level=A1&status=pending_review")) == ids_where(
        models.Question.stage == "pretest", models.Question.skill == "grammar", models.Question.level == "A1",
        models.Question.review_status == "pending_review")
    assert listed(web.get("/admin/questions?q=Q002")) == ["Q002"]
    assert listed(web.get("/admin/questions?q=你好")) == ids_where(models.Question.question_zh.contains("你好"))
    assert listed(web.get("/admin/questions?stage=hack%22%3E")) == all_ids[:25], "unknown filter values are ignored"
    assert "No questions match" in web.get("/admin/questions?q=%25").text, "% is a letter, not a wildcard"
    with dbmod.write_session() as s:
        s.get(models.Question, "Q003").review_status = "approved"
    assert listed(web.get("/admin/questions?status=approved")) == ["Q003"]
    assert "Q003" not in listed(web.get("/admin/questions?status=pending_review&stage=pretest"))
    assert "stage=pretest" in web.get("/admin/questions?stage=pretest&page=1").text  # filters survive in the review forms' return link
    with dbmod.write_session() as s:  # a second page keeps its filters in the pager links
        for i in range(30):
            s.add(models.Question(id=f"Q{500 + i}", stage="posttest", level="A2", skill="reading", question_zh="長", options=list("abcd"),
                                  correct_index=0))
    nxt = re.search(r'href="(/admin/questions\?[^"]+)">Next', web.get("/admin/questions?skill=reading").text)
    assert nxt and "skill=reading" in nxt.group(1) and "page=2" in nxt.group(1)


def test_question_text_is_escaped(web, seeded):
    token = as_user(web, new_user("teacher"))
    r = post_q(web, token, "/admin/questions/new", question_zh="<script>alert(1)</script>")
    assert r.status_code == 303
    qid = r.headers["location"].rsplit("/", 1)[1]
    assert "<script>alert(1)</script>" not in web.get(f"/admin/questions/{qid}").text
    assert "<script>alert(1)</script>" not in web.get("/admin/questions?q=alert").text
    assert "&lt;script&gt;" in web.get(f"/admin/questions/{qid}").text


def test_create_question_gets_next_id_and_waits_for_review(web, seeded):
    teacher = new_user("teacher")
    token = as_user(web, teacher)
    r = post_q(web, token, "/admin/questions/new")
    assert r.status_code == 303 and r.headers["location"] == "/admin/questions/Q051"
    q = read(lambda s: s.get(models.Question, "Q051"))
    assert q.review_status == "pending_review" and q.options == ["uống", "ăn", "mua", "bán"] and q.correct_index == 0
    assert q.topic == "喝" and q.reviewer_id is None and q.active
    assert audit_actions()[-1] == "question_created"
    assert post_q(web, token, "/admin/questions/new").headers["location"].endswith("Q052")


@pytest.mark.parametrize("over,code", [
    ({"question_zh": ""}, "This field is required."),
    ({"option_3": ""}, "No option may be empty."),
    ({"option_3": "uống"}, "must all be different"),
    ({"correct": ""}, "Choose which option is correct."),
    ({"correct": "7"}, "Choose which option is correct."),
    ({"level": "C9"}, "not an allowed value"),
    ({"skill": ""}, "not an allowed value"),
    ({"stage": "final"}, "not an allowed value"),
])
def test_form_validation_errors_do_not_save(web, seeded, over, code):
    token = as_user(web, new_user("teacher"))
    before = (count(models.Question), count(models.AuditLog))
    r = post_q(web, token, "/admin/questions/new", **over)
    assert r.status_code == 422 and code in r.text and "Nothing was saved" in r.text
    assert (count(models.Question), count(models.AuditLog)) == before
    old = read(lambda s: s.get(models.Question, "Q001").question_zh)
    r = post_q(web, token, "/admin/questions/Q001", **over)
    assert r.status_code == 422 and code in r.text
    assert read(lambda s: s.get(models.Question, "Q001").question_zh) == old
    # the form keeps what the teacher typed
    assert "uống" in post_q(web, token, "/admin/questions/Q001", **over).text


def test_edit_saves_fields_and_audits(web, seeded):
    token = as_user(web, new_user("teacher"))
    r = post_q(web, token, "/admin/questions/Q001", question_zh="新題目", correct="2", explanation_en="new", review_note="checked tone")
    assert r.status_code == 303 and r.headers["location"] == "/admin/questions/Q001"
    q = read(lambda s: s.get(models.Question, "Q001"))
    assert (q.question_zh, q.correct_index, q.explanation_en, q.review_note) == ("新題目", 2, "new", "checked tone")
    assert q.options == ["uống", "ăn", "mua", "bán"] and q.review_status == "pending_review"
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert entry.action == "question_edited" and entry.target == "Q001" and "question_zh" in entry.detail["fields"]


@pytest.mark.parametrize("status", ["approved", "needs_edit", "pending_review"])
@pytest.mark.parametrize("role", ["teacher", "admin"])
def test_one_click_review_sets_reviewer_time_and_audit(web, seeded, status, role):
    user = new_user(role)
    token = as_user(web, user)
    start = utcnow()
    r = web.post("/admin/questions/Q004/review", data={"csrf": token, "status": status})
    assert r.status_code == 303 and r.headers["location"] == "/admin/questions/Q004"
    q = read(lambda s: s.get(models.Question, "Q004"))
    assert q.review_status == status and q.reviewer_id == user.id and q.reviewed_at >= start - timedelta(seconds=1)
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert (entry.action, entry.target, entry.actor_id, entry.detail["to"]) == ("question_review", "Q004", user.id, status)
    assert user.public_id in web.get("/admin/questions/Q004").text, "the edit page names the last reviewer"


def test_review_rejects_bad_status_and_unsafe_next(web, seeded):
    token = as_user(web, new_user("teacher"))
    r = web.post("/admin/questions/Q001/review", data={"csrf": token, "status": "hacked"})
    assert r.status_code == 303 and read(lambda s: s.get(models.Question, "Q001").review_status) == "pending_review"
    for bad in ("//evil.example", "https://evil.example", "/admin/questions//evil", "/other"):
        r = web.post("/admin/questions/Q001/review", data={"csrf": token, "status": "approved", "next": bad})
        assert r.headers["location"] == "/admin/questions/Q001", bad
    r = web.post("/admin/questions/Q001/review", data={"csrf": token, "status": "approved", "next": "/admin/questions?stage=daily&page=2"})
    assert r.headers["location"] == "/admin/questions?stage=daily&page=2"


def test_editing_approved_content_sends_it_back_to_review(web, seeded):
    token = as_user(web, new_user("teacher"))
    web.post("/admin/questions/Q001/review", data={"csrf": token, "status": "approved"})
    q = read(lambda s: s.get(models.Question, "Q001"))
    keep = dict(question_zh=q.question_zh, option_0=q.options[0], option_1=q.options[1], option_2=q.options[2], option_3=q.options[3],
                correct=str(q.correct_index), topic=q.topic or "", level=q.level, skill=q.skill, stage=q.stage,
                explanation_vi=q.explanation_vi or "", explanation_zh=q.explanation_zh or "", explanation_en="")
    r = post_q(web, token, "/admin/questions/Q001", **keep, review_note="only a note")
    assert r.status_code == 303 and read(lambda s: s.get(models.Question, "Q001").review_status) == "approved"
    r = post_q(web, token, "/admin/questions/Q001", **{**keep, "question_zh": q.question_zh + "！"})
    after = read(lambda s: s.get(models.Question, "Q001"))
    assert after.review_status == "pending_review" and after.reviewer_id is None and after.reviewed_at is None


def test_banner_explains_production_rule(web, seeded, monkeypatch):
    as_user(web, new_user("teacher"))
    assert "allow_unreviewed_questions is ON" in web.get("/admin/questions", headers={"accept-language": "en"}).text
    monkeypatch.setenv("HUAYU_ALLOW_UNREVIEWED_QUESTIONS", "false")
    get_settings.cache_clear()
    assert "allow_unreviewed_questions is OFF" in web.get("/admin/questions", headers={"accept-language": "en"}).text


# ============================================================================ import / export
def upload(web, token, payload: bytes | str | list, name="q.json"):
    if not isinstance(payload, (bytes, str)):
        payload = json.dumps(payload, ensure_ascii=False)
    data = payload.encode() if isinstance(payload, str) else payload
    return web.post("/admin/questions/import", data={"csrf": token}, files={"file": (name, io.BytesIO(data), "application/json")})


def item(qid="Q900", **over):
    base = {"id": qid, "stage": "daily", "day": 3, "level": "A2", "skill": "grammar", "topic": "了", "question_zh": "我吃__飯了。",
            "options": ["了", "在", "會", "是"], "correct_index": 0, "explanation_vi": "v", "explanation_zh": None,
            "review_status": "approved", "review_note": None, "source": "teacher file"}
    base.update(over)
    return base


def test_import_valid_file_adds_pending_questions_and_audits(web, seeded):
    admin = new_user("teacher")
    token = as_user(web, admin)
    r = upload(web, token, [item("Q900"), item("Q901", question_zh="另一題")])
    assert r.status_code == 200 and "<b>2</b>Added" in r.text and "<b>0</b>Skipped" in r.text
    q = read(lambda s: s.get(models.Question, "Q900"))
    assert q.review_status == "pending_review", "an imported file is never trusted as reviewed"
    assert (q.reviewer_id, q.reviewed_at, q.day, q.source, q.topic, q.level) == (None, None, 3, "teacher file", "了", "A2")
    assert count(models.Question) == 52
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert entry.action == "questions_imported" and entry.detail == {"added": 2, "skipped": 0, "invalid": 0}


def test_import_skips_existing_and_duplicate_ids_and_reports_invalid(web, seeded):
    token = as_user(web, new_user("admin"))
    original = read(lambda s: s.get(models.Question, "Q001").question_zh)
    payload = [item("Q001", question_zh="OVERWRITE"), item("Q910"), item("Q910", question_zh="dup in file"),
               item("Q911", options=["a", "a", "b", "c"]), item("Q912", correct_index=9), {"id": "bad id!"}, "not an object", item("Q913")]
    r = upload(web, token, payload)
    assert r.status_code == 200
    assert read(lambda s: s.get(models.Question, "Q001").question_zh) == original
    assert read(lambda s: s.get(models.Question, "Q910").question_zh) == "我吃__飯了。"
    ids = read(lambda s: set(s.scalars(select(models.Question.id))))
    assert {"Q910", "Q913"} <= ids and not ({"Q911", "Q912"} & ids)
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert entry.detail == {"added": 2, "skipped": 2, "invalid": 4}
    text = web.post("/admin/questions/import", data={"csrf": token}, files={"file": ("q.json", json.dumps(payload).encode(), "application/json")},
                    headers={"accept-language": "en"}).text
    assert "The four options must all be different." in text and "Choose which option is correct." in text
    assert "Q911" in text and "Q912" in text and "#6" in text and "#7" in text and "not a question object" in text


def test_import_report_function_counts():
    class FakeDB:
        def scalars(self, stmt):
            return iter(["Q001"])

        def add(self, obj):
            self.added = getattr(self, "added", 0) + 1
    rep = admin_mod.import_questions(FakeDB(), json.dumps([item("Q001"), item("Q002"), item("Q003", stage="x")]).encode())
    assert (rep.added, rep.skipped, len(rep.invalid), rep.fatal) == (1, 1, 1, None)
    assert rep.invalid == [("Q003", {"stage": "invalid_choice"})]


@pytest.mark.parametrize("payload,fatal", [
    (b"{not json", "bad_json"), (b"\xff\xfe\x00", "bad_json"), (b"[" * 50000, "bad_json"), (b'{"id": "Q1"}', "bad_format"),
    (b'"text"', "bad_format"), (json.dumps([item(f"Q{i}") for i in range(501)]).encode(), "too_many"),
    (b" " * (1024 * 1024 + 1), "too_large")])
def test_import_refuses_whole_file(web, seeded, payload, fatal):
    token = as_user(web, new_user("admin"))
    r = upload(web, token, payload)
    assert r.status_code == 422 and "Nothing was imported" in web.post(
        "/admin/questions/import", data={"csrf": token}, files={"file": ("q.json", payload, "application/json")},
        headers={"accept-language": "en"}).text
    assert count(models.Question) == 50
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert entry.action == "questions_imported" and entry.detail["error"] == fatal


def test_import_500_items_is_allowed_and_huge_upload_is_cut_early(web, seeded):
    token = as_user(web, new_user("admin"))
    r = upload(web, token, [item(f"Q{1000 + i}") for i in range(500)])
    assert r.status_code == 200 and count(models.Question) == 550
    r = web.post("/admin/questions/import", data={"csrf": token}, files={"file": ("q.json", b"[" + b" " * (3 * 1024 * 1024) + b"]", "application/json")})
    assert r.status_code == 413 and count(models.Question) == 550


def test_import_without_a_file_redirects_with_a_message(web, seeded):
    token = as_user(web, new_user("admin"))
    r = web.post("/admin/questions/import", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/admin/questions"
    # a text field where a file is expected is rejected by request validation before anything touches the database
    assert web.post("/admin/questions/import", data={"csrf": token, "file": "just text"}).status_code == 422


def test_export_has_seed_shape_and_round_trips_through_import(web, seeded):
    token = as_user(web, new_user("teacher"))
    r = web.get("/admin/questions/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    exported = r.json()
    seeds = json.loads(SEEDS.read_text(encoding="utf-8"))
    assert [q["id"] for q in exported] == [q["id"] for q in seeds]
    assert set(seeds[0]) <= set(exported[0])
    for mine, theirs in zip(exported, seeds):
        for key, value in theirs.items():
            assert mine[key] == value, (theirs["id"], key)
    with dbmod.write_session() as s:
        s.query(models.Question).delete()
    assert count(models.Question) == 0
    rep = upload(web, token, r.content)
    assert rep.status_code == 200 and count(models.Question) == 50
    again = web.get("/admin/questions/export").json()
    assert [{k: v for k, v in q.items() if k != "review_status"} for q in again] == [{k: v for k, v in q.items() if k != "review_status"} for q in exported]
    assert {q["review_status"] for q in again} == {"pending_review"}
    # re-importing the same file changes nothing
    assert upload(web, token, r.content).status_code == 200 and count(models.Question) == 50


# ============================================================================ users, roles, sessions
def test_user_list_search_and_filters(web):
    admin = new_user("admin")
    learner = new_user("learner", email="needle@example.com")
    other = new_user("learner", email="other@example.com")
    as_user(web, admin)
    r = web.get("/admin/users")
    assert r.status_code == 200 and learner.public_id in r.text and "needle@example.com" in r.text
    assert "needle@example.com" in web.get(f"/admin/users?q={learner.public_id.lower()}").text
    assert "other@example.com" not in web.get("/admin/users?q=needle").text
    assert "other@example.com" not in web.get(f"/admin/users?q={learner.public_id}").text
    assert "needle@example.com" not in web.get("/admin/users?role=admin").text
    assert "No accounts match" in web.get("/admin/users?q=%25", headers={"accept-language": "en"}).text
    assert web.get("/admin/users?role=bogus&page=-4").status_code == 200
    assert other.public_id


def test_user_detail_shows_profile_history_but_never_free_text(web):
    admin = new_user("admin")
    learner = new_user("learner", email="detail@example.com")
    secret_report, secret_comment = "MY-PRIVATE-WORDS-123", "PRIVATE-COMMENT-456"
    with dbmod.write_session() as s:
        s.merge(models.BillingPlan(code="monthly", name={"vi": "Tháng", "zh-TW": "月", "en": "Monthly"}, price_twd=199, period_days=30))
        it = models.Interruption(user_id=learner.id, reason_code="no_time", self_report=secret_report, status="declined")
        s.add(it)
        s.flush()
        rec = models.Recommendation(interruption_id=it.id, user_id=learner.id, base_plan_version=1, status="rejected")
        s.add(rec)
        s.flush()
        s.add(models.Feedback(user_id=learner.id, recommendation_id=rec.id, clarity=4, helpfulness=4, pressure=2, comment=secret_comment))
        billing.activate(s, s.get(models.User, learner.id), "monthly", actor=s.get(models.User, admin.id), note="bank transfer ref 77")
    as_user(web, admin)
    r = web.get(f"/admin/users/{learner.id}", headers={"accept-language": "en"})
    assert r.status_code == 200
    for expected in ("detail@example.com", learner.public_id, "No time", "Declined", "bank transfer ref 77", "NT$ 199", "monthly"):
        assert expected in r.text, expected
    for page in (f"/admin/users/{learner.id}", "/admin", "/admin/users", "/admin/audit", "/admin/billing", "/admin/questions"):
        body = web.get(page).text
        assert secret_report not in body and secret_comment not in body and "password" not in body.lower().replace("passwords", "")
    assert "$argon2" not in r.text and "x</td>" not in r.text


def test_grant_and_activate_change_access(web, billing_on):
    admin = new_user("admin")
    learner = new_user("learner")
    with dbmod.write_session() as s:
        billing.seed_plans(s)
    token = as_user(web, admin)

    def state():
        return read(lambda s: billing.access(s, s.get(models.User, learner.id)))

    assert state().state == "expired" and not state().allowed
    r = web.post(f"/admin/users/{learner.id}/grant", data={"csrf": token, "days": "9", "note": "support gesture"})
    assert r.status_code == 303 and r.headers["location"] == f"/admin/users/{learner.id}"
    st = state()
    assert st.state == "trialing" and st.allowed and timedelta(days=8, hours=23) < st.until - utcnow() <= timedelta(days=9)
    r = web.post(f"/admin/users/{learner.id}/activate", data={"csrf": token, "plan_code": "monthly", "amount": "150", "note": "ref 42"})
    assert r.status_code == 303
    st = state()
    assert st.state == "active" and timedelta(days=38, hours=23) < st.until - utcnow() <= timedelta(days=39), "extends from the granted end"
    pay = read(lambda s: s.scalars(select(models.Payment).where(models.Payment.user_id == learner.id)).one())
    assert (pay.amount_twd, pay.note, pay.provider) == (150, "ref 42", "manual")
    web.post(f"/admin/users/{learner.id}/activate", data={"csrf": token, "plan_code": "semester"})
    assert read(lambda s: s.scalars(select(models.Payment.amount_twd).order_by(models.Payment.id.desc())).first()) == 799  # plan price by default
    actions = audit_actions()
    assert actions.count("access_granted") == 1 and actions.count("subscription_activated") == 2
    entry = read(lambda s: s.scalars(select(models.AuditLog).where(models.AuditLog.action == "access_granted")).one())
    assert entry.actor_id == admin.id and entry.target == learner.public_id and entry.detail["days"] == 9


@pytest.mark.parametrize("path,form", [
    ("grant", {"days": "0"}), ("grant", {"days": "366"}), ("grant", {"days": "abc"}), ("grant", {"days": ""}),
    ("activate", {"plan_code": "nope"}), ("activate", {"plan_code": ""}), ("activate", {"plan_code": "monthly", "amount": "-5"}),
    ("activate", {"plan_code": "monthly", "amount": "1e3"}), ("activate", {"plan_code": "monthly", "amount": "99999999"}),
])
def test_grant_and_activate_validate_input(web, billing_on, path, form):
    admin, learner = new_user("admin"), new_user("learner")
    with dbmod.write_session() as s:
        billing.seed_plans(s)
    token = as_user(web, admin)
    r = web.post(f"/admin/users/{learner.id}/{path}", data={"csrf": token, **form})
    assert r.status_code == 303
    assert count(models.Subscription) == 0 and count(models.Payment) == 0 and count(models.AuditLog) == 0
    assert "hc_flash" in r.headers["set-cookie"], "the admin is told why nothing happened"


def test_activate_refuses_inactive_plan_and_staff_targets(web, billing_on):
    admin, teacher = new_user("admin"), new_user("teacher")
    learner = new_user("learner")
    with dbmod.write_session() as s:
        billing.seed_plans(s)
        s.get(models.BillingPlan, "monthly").active = False
    token = as_user(web, admin)
    web.post(f"/admin/users/{learner.id}/activate", data={"csrf": token, "plan_code": "monthly"})
    web.post(f"/admin/users/{teacher.id}/grant", data={"csrf": token, "days": "5"})
    web.post(f"/admin/users/{teacher.id}/activate", data={"csrf": token, "plan_code": "semester"})
    assert count(models.Subscription) == 0 and count(models.Payment) == 0


def test_change_role_rules(session):
    admin = make_user(session, role="admin")
    other_admin = make_user(session, role="admin")
    learner = make_user(session)
    teacher = make_user(session, role="teacher")
    with pytest.raises(admin_mod.AdminError, match="own_role"):
        admin_mod.change_role(session, admin, admin, "learner")
    with pytest.raises(admin_mod.AdminError, match="invalid_role"):
        admin_mod.change_role(session, admin, learner, "owner")
    assert admin_mod.change_role(session, admin, learner, "learner") is False
    assert admin_mod.change_role(session, admin, learner, "teacher") is True and learner.role == "teacher"
    assert admin_mod.change_role(session, admin, other_admin, "learner") is True  # admin is now the only one left
    with pytest.raises(admin_mod.AdminError, match="last_admin"):
        admin_mod.change_role(session, teacher, admin, "teacher")  # e.g. a stale or forged request
    assert admin.role == "admin"
    assert session.scalars(select(models.AuditLog.action)).all() == ["role_changed", "role_changed"]


def test_role_change_through_the_page(web):
    admin = new_user("admin")
    learner = new_user("learner")
    token = as_user(web, admin)
    r = web.post(f"/admin/users/{learner.id}/role", data={"csrf": token, "role": "teacher"})
    assert r.status_code == 303 and read(lambda s: s.get(models.User, learner.id).role) == "teacher"
    entry = read(lambda s: s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc())).first())
    assert (entry.action, entry.target, entry.detail) == ("role_changed", learner.public_id, {"from": "learner", "to": "teacher"})
    # cannot change your own role, cannot pick an unknown role
    web.post(f"/admin/users/{admin.id}/role", data={"csrf": token, "role": "learner"})
    web.post(f"/admin/users/{learner.id}/role", data={"csrf": token, "role": "owner"})
    assert read(lambda s: s.get(models.User, admin.id).role) == "admin" and read(lambda s: s.get(models.User, learner.id).role) == "teacher"
    assert count(models.AuditLog) == 1
    # the form for your own account explains why there is no button
    assert "cannot change your own role" in web.get(f"/admin/users/{admin.id}", headers={"accept-language": "en"}).text
    # a demoted admin loses access immediately
    second = new_user("admin")
    web.post(f"/admin/users/{second.id}/role", data={"csrf": token, "role": "learner"})
    as_user(web, second)
    assert web.get("/admin/users").status_code == 403


def test_end_all_sessions_logs_the_learner_out(web):
    admin, learner = new_user("admin"), new_user("learner")
    token = as_user(web, admin)
    assert web.post(f"/admin/users/{learner.id}/sessions", data={"csrf": token}).status_code == 303
    assert read(lambda s: s.get(models.User, learner.id).session_version) == 2
    as_user(web, learner)  # a cookie made for the OLD session version
    web.cookies.set("hc_session", __import__("app.security", fromlist=["x"]).make_session_token(learner.id, 1, learner.public_id))
    r = web.get("/billing")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert audit_actions() == ["sessions_ended"]


# ============================================================================ plans, payments, audit
def plan_data(**over):
    return {"name_vi": "Tháng", "name_zh": "月方案", "name_en": "Monthly", "price_twd": "250", "period_days": "30", **over}


def test_plan_edit_create_and_validation(web):
    admin = new_user("admin")
    with dbmod.write_session() as s:
        billing.seed_plans(s)
    token = as_user(web, admin)
    assert "monthly" in web.get("/admin/billing").text
    r = web.post("/admin/billing/plans/monthly", data={"csrf": token, **plan_data(), "active": "1"})
    assert r.status_code == 303
    p = read(lambda s: s.get(models.BillingPlan, "monthly"))
    assert (p.price_twd, p.period_days, p.active, p.name["zh-TW"]) == (250, 30, True, "月方案")
    web.post("/admin/billing/plans/monthly", data={"csrf": token, **plan_data()})  # checkbox absent = hidden
    assert read(lambda s: s.get(models.BillingPlan, "monthly").active) is False
    for bad in ({"price_twd": "-1"}, {"price_twd": "abc"}, {"price_twd": "1000001"}, {"period_days": "0"}, {"period_days": "3651"},
                {"name_vi": ""}, {"name_en": "x" * 61}):
        web.post("/admin/billing/plans/semester", data={"csrf": token, **plan_data(**bad), "active": "1"})
        assert read(lambda s: s.get(models.BillingPlan, "semester").price_twd) == 799, bad
    r = web.post("/admin/billing/plans", data={"csrf": token, "code": "yearly", **plan_data(price_twd="1500", period_days="365")})
    assert r.status_code == 303
    y = read(lambda s: s.get(models.BillingPlan, "yearly"))
    assert (y.price_twd, y.period_days, y.active) == (1500, 365, True)
    for bad_code in ("Yearly", "1year", "y", "has space", "x" * 21, "yearly"):
        web.post("/admin/billing/plans", data={"csrf": token, "code": bad_code, **plan_data()})
    assert count(models.BillingPlan) == 3
    assert audit_actions().count("plan_created") == 1 and audit_actions().count("plan_saved") == 2


def test_billing_admin_totals_per_month_and_recent_payments(web):
    admin, learner = new_user("admin"), new_user("learner")
    with dbmod.write_session() as s:
        s.add_all([models.Payment(user_id=learner.id, amount_twd=199, created_at=datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc), note="late oct utc"),
                   models.Payment(user_id=learner.id, amount_twd=800, created_at=datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)),
                   models.Payment(user_id=None, amount_twd=50, created_at=datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc))])
    as_user(web, admin)
    r = web.get("/admin/billing", headers={"accept-language": "en"})
    assert r.status_code == 200 and "late oct utc" in r.text
    months = read(lambda s: admin_stats.monthly_revenue(s, now=datetime(2026, 11, 18, tzinfo=timezone.utc)))
    assert months == [("2026-11", 999, 2), ("2026-10", 50, 1)], "Taipei time: 17:00 UTC on 31 Oct already belongs to November"
    assert "NT$ 999" in r.text and "NT$ 50" in r.text and "2026-11" in r.text and "2026-10" in r.text


def test_audit_page_is_paginated_and_escaped(web):
    admin = new_user("admin")
    with dbmod.write_session() as s:
        for i in range(120):
            s.add(models.AuditLog(actor_id=admin.id, action="demo", target=f"T{i:03d}", detail={"note": "<b>x</b>"}))
        s.add(models.AuditLog(actor_id=None, action="account_deleted", target="LABC234", detail={}))
    as_user(web, admin)
    first = web.get("/admin/audit", headers={"accept-language": "en"}).text
    assert first.count('class="mono">') == 50 and "T119" in first and "T070" not in first and "Page 1 of 3" in first
    assert "(account deleted)" in first and "<b>x</b>" not in first and "&lt;b&gt;x&lt;/b&gt;" in first
    assert web.get("/admin/audit?page=3").text.count('class="mono">') == 21
    assert web.get("/admin/audit?page=99").status_code == 200 and web.get("/admin/audit?page=x").status_code == 200


# ============================================================================ dashboard numbers
def _interruption(db, user, reason, status, source="actual", when=None):
    it = models.Interruption(user_id=user.id, reason_code=reason, status=status, source=source, created_at=when or utcnow())
    db.add(it)
    db.flush()
    return it


def _rec(db, it, status):
    rec = models.Recommendation(interruption_id=it.id, user_id=it.user_id, base_plan_version=1, status=status)
    db.add(rec)
    db.flush()
    return rec


def _fb(db, rec, c, h, p):
    db.add(models.Feedback(user_id=rec.user_id, recommendation_id=rec.id, clarity=c, helpfulness=h, pressure=p))
    db.flush()


def test_stats_with_no_data_are_none_never_zero(session):
    d = admin_stats.dashboard(session)
    assert (d.learners_total, d.learners_active_7d, d.interruptions_total, d.proposals_total) == (0, 0, 0, 0)
    for avg in (d.clarity, d.helpfulness, d.pressure):
        assert avg.n == 0 and avg.value is None and avg.meets_target is None
    assert d.accepted_share.value is None and d.accepted_share.n == 0 and d.return_rate.value is None
    assert d.interruptions_by_reason == {"too_hard": 0, "no_time": 0, "need_rest": 0, "distracted": 0}
    assert d.interruptions_by_status == {"proposed": 0, "accepted": 0, "declined": 0, "resumed": 0}
    assert d.proposals_by_status == dict.fromkeys(("accepted", "partial", "rejected", "superseded", "pending"), 0)
    assert d.question_bank == {"approved": 0, "pending_review": 0, "needs_edit": 0, "total": 0}
    assert d.subscriptions == {"trialing": 0, "active": 0, "expired": 0, "none": 0}
    assert admin_stats.monthly_revenue(session) == []


def test_stats_numbers_match_hand_built_data(session):
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    a, b, c = (make_user(session) for _ in range(3))
    teacher = make_user(session, role="teacher")
    add_attempt(session, a, "Q1", correct=True, when=now - timedelta(days=2))
    add_attempt(session, a, "Q2", correct=False, when=now - timedelta(days=1))  # same learner twice = one active learner
    add_attempt(session, b, "Q1", correct=True, when=now - timedelta(days=10))
    add_attempt(session, teacher, "Q1", correct=True, when=now - timedelta(days=1))  # staff do not count
    assert admin_stats.learner_counts(session, now) == (3, 1)

    i1 = _interruption(session, a, "too_hard", "accepted")
    i2 = _interruption(session, b, "no_time", "declined")
    i3 = _interruption(session, a, "too_hard", "resumed")
    i4 = _interruption(session, c, "need_rest", "proposed")
    sim = _interruption(session, c, "distracted", "resumed", source="simulated")
    r1, r2, r3 = _rec(session, i1, "accepted"), _rec(session, i2, "rejected"), _rec(session, i3, "partial")
    r4, r5 = _rec(session, i4, "pending"), _rec(session, i4, "superseded")
    rs = _rec(session, sim, "accepted")
    _fb(session, r1, 5, 4, 2)
    _fb(session, r2, 4, 4, 1)
    _fb(session, r3, 3, 5, 3)
    _fb(session, rs, 1, 1, 5)  # simulated: ignored
    d = admin_stats.dashboard(session, now)
    assert d.interruptions_by_reason == {"too_hard": 2, "no_time": 1, "need_rest": 1, "distracted": 0}
    assert d.interruptions_by_status == {"proposed": 1, "accepted": 1, "declined": 1, "resumed": 1}
    assert d.interruptions_total == 4 and (d.return_rate.part, d.return_rate.total, d.return_rate.value) == (1, 4, 0.25)
    assert d.proposals_by_status == {"accepted": 1, "partial": 1, "rejected": 1, "superseded": 1, "pending": 1} and d.proposals_total == 5
    assert (d.clarity.n, d.clarity.value, d.clarity.meets_target) == (3, 4.0, True)
    assert (d.helpfulness.n, d.helpfulness.value, d.helpfulness.meets_target) == (3, 4.33, True)
    assert (d.pressure.n, d.pressure.value) == (3, 2.0)
    assert (d.accepted_share.part, d.accepted_share.total) == (2, 3) and round(d.accepted_share.value, 3) == 0.667
    assert r4.status == "pending"


def test_rating_below_target_is_flagged(session):
    u = make_user(session)
    it = _interruption(session, u, "too_hard", "accepted")
    _fb(session, _rec(session, it, "rejected"), 3, 3, 4)
    d = admin_stats.dashboard(session)
    assert d.clarity.meets_target is False and d.accepted_share.value == 0.0 and d.accepted_share.n == 1


def test_question_bank_and_subscription_counts(session):
    seed_all(session)
    seeded_status = [q["review_status"] for q in json.loads(SEEDS.read_text(encoding="utf-8"))]
    assert seeded_status.count("approved") == 0 and seeded_status.count("needs_edit") == 6
    session.get(models.Question, "Q001").review_status = "approved"
    session.get(models.Question, "Q002").review_status = "needs_edit"
    session.flush()
    expected = {"approved": 1, "pending_review": seeded_status.count("pending_review") - 2, "needs_edit": 6 + 1, "total": 50}
    assert expected["pending_review"] + expected["needs_edit"] == 49
    assert admin_stats.question_bank(session) == expected
    now = utcnow()
    trial, paid, lapsed, never, old_then_paid = (make_user(session) for _ in range(5))
    staff = make_user(session, role="teacher")
    session.add_all([
        models.Subscription(user_id=trial.id, status="trialing", started_at=now, current_period_end=now + timedelta(days=3)),
        models.Subscription(user_id=paid.id, plan_code="monthly", status="active", started_at=now, current_period_end=now + timedelta(days=20)),
        models.Subscription(user_id=lapsed.id, status="trialing", started_at=now - timedelta(days=30), current_period_end=now - timedelta(days=1)),
        models.Subscription(user_id=old_then_paid.id, status="trialing", started_at=now - timedelta(days=30), current_period_end=now - timedelta(days=2)),
        models.Subscription(user_id=old_then_paid.id, plan_code="monthly", status="active", started_at=now, current_period_end=now + timedelta(days=9)),
        models.Subscription(user_id=staff.id, status="trialing", started_at=now, current_period_end=now + timedelta(days=3))])
    session.flush()
    assert admin_stats.subscription_counts(session, now) == {"trialing": 1, "active": 2, "expired": 1, "none": 1}


# ============================================================================ dashboard page
def test_dashboard_page_for_admin_and_teacher(web, seeded):
    learner, teacher, admin = new_user(), new_user("teacher"), new_user("admin")
    with dbmod.write_session() as s:
        add_attempt(s, learner, "Q1", correct=True)
        it = _interruption(s, learner, "too_hard", "resumed")
        _fb(s, _rec(s, it, "accepted"), 5, 4, 2)
    as_user(web, admin)
    en = web.get("/admin", headers={"accept-language": "en"}).text
    assert "Pilot results" in en and "n = 1" in en and "5.0" in en and "100%" in en and "Too hard" in en and "Question bank" in en
    as_user(web, teacher)
    t = web.get("/admin", headers={"accept-language": "en"}).text
    assert "Question bank" in t and "Pilot results" not in t and "Learners in total" not in t and "Why learners stopped" not in t
    assert 'href="/admin/users"' not in t and 'href="/admin/audit"' not in t and 'href="/admin/billing"' not in t


@pytest.mark.parametrize("loc,wording", [("en", "No data yet"), ("vi", "Chưa có dữ liệu"), ("zh-TW", "尚無資料")])
def test_dashboard_without_data_says_no_data_not_zero_percent(web, loc, wording):
    admin = new_user("admin", locale=loc)
    as_user(web, admin)
    r = web.get("/admin", headers={"accept-language": loc})
    assert wording in r.text and "0%" not in r.text and "0.0" not in r.text


# ============================================================================ all pages, all languages, no raw keys
ALL_KEYS = sorted(k for k in i18n.catalog("en") if k.startswith(("admin.", "billing.")))


@pytest.mark.parametrize("loc", LOCALES)
def test_every_page_renders_in_every_language_without_raw_keys(web, seeded, billing_on, loc):
    with dbmod.write_session() as s:
        billing.seed_plans(s)
        admin = make_user(s, role="admin")
        admin.locale = loc
        learner = make_user(s)
        it = _interruption(s, learner, "no_time", "accepted")
        _fb(s, _rec(s, it, "partial"), 4, 4, 2)
        billing.activate(s, learner, "monthly", actor=admin)
        s.add(models.Payment(user_id=learner.id, amount_twd=10))
    as_user(web, admin)
    pages = ["/admin", "/admin/questions", "/admin/questions?status=needs_edit", "/admin/questions/new", "/admin/questions/Q002",
             "/admin/users", f"/admin/users/{learner.id}", f"/admin/users/{admin.id}", "/admin/billing", "/admin/audit", "/billing"]
    for path in pages:
        r = web.get(path)
        assert r.status_code == 200, path
        assert f'lang="{loc}"' in r.text
        assert_balanced(r.text, path)
        leaked = [k for k in ALL_KEYS if k in r.text]
        assert not leaked, (loc, path, leaked[:5])
    token = login_as(web, admin)
    r = post_q(web, token, "/admin/questions/Q002", question_zh="")  # validation page
    assert r.status_code == 422 and not [k for k in ALL_KEYS if k in r.text]
    assert_balanced(r.text, "question form with errors")
    r = upload(web, token, b"{bad")
    assert r.status_code == 422 and not [k for k in ALL_KEYS if k in r.text]
    r = upload(web, token, [item("Q990"), item("Q991", stage="x")])
    assert r.status_code == 200 and not [k for k in ALL_KEYS if k in r.text]
    assert_balanced(r.text, "import result")
    # flash messages (set by redirects) render too
    web.post("/admin/billing/plans/monthly", data={"csrf": token, "price_twd": "x"})
    assert not [k for k in ALL_KEYS if k in web.get("/admin/billing").text]


def test_every_translation_key_used_in_templates_and_routers_exists():
    root = Path(admin_mod.__file__).resolve().parent.parent
    used = set()
    for path in list((root / "templates" / "admin").glob("*.html")) + list((root / "templates" / "billing").glob("*.html")):
        used |= set(re.findall(r"""\bt\('((?:admin|billing)\.[a-z_0-9.]+)'(?! ~)""", path.read_text(encoding="utf-8")))
    used |= set(re.findall(r'flash="(admin\.[a-z_0-9.]+)"', (root / "routers" / "admin.py").read_text(encoding="utf-8")))
    used |= {f"admin.flash.err_{c}" for c in ("invalid_role", "own_role", "last_admin")}
    assert len(used) > 100
    for loc in LOCALES:
        missing = [k for k in used if i18n.t(loc, k) == k]
        assert not missing, (loc, missing)
    # dynamic key families
    families = {"admin.role.": admin_mod.ROLES, "admin.stage.": admin_mod.STAGES, "admin.skill.": admin_mod.SKILLS,
                "admin.review.": admin_mod.REVIEW_STATUSES, "admin.reason.": ("too_hard", "no_time", "need_rest", "distracted"),
                "admin.istatus.": admin_stats.INTERRUPTION_STATUSES, "admin.pstatus.": admin_stats.PROPOSAL_STATUSES,
                "admin.sub.": admin_stats.SUBSCRIPTION_STATES, "admin.access.": ("free", "staff", "trialing", "active", "expired"),
                "admin.import.fatal.": ("too_large", "bad_json", "bad_format", "too_many"),
                "billing.state.": tuple(f"{s}.title" for s in ("free", "staff", "trialing", "active", "expired"))}
    for loc in LOCALES:
        for prefix, values in families.items():
            for v in values:
                assert i18n.t(loc, prefix + v) != prefix + v, (loc, prefix, v)
