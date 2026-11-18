"""Slice A (auth): register, login/logout, forgotten password, and the shared rules (e-mail, password, ?next=)."""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES, get_settings
from app.security import hash_password, hash_token, limiter, verify_password
from app.timeutil import utcnow
from tests.conftest import login_as, make_user

PASSWORD = "correct horse battery"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _only_my_routers(monkeypatch):
    monkeypatch.setenv("HUAYU_ROUTERS", "auth,account")


# ----------------------------------------------------------------------------- helpers
def csrf(client) -> str:
    """Visit a page so the app issues the CSRF cookie, return its value."""
    client.get("/login")
    return client.cookies.get("hc_csrf")


def settings_env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(f"HUAYU_{key.upper()}", str(value))
    get_settings.cache_clear()


def add_user(email="ana@example.com", password=PASSWORD, **kw) -> models.User:
    with dbmod.write_session() as db:
        user = make_user(db, email=email, **kw)
        user.password_hash = hash_password(password)
        db.flush()
        return user


def get_user(email: str) -> models.User | None:
    with dbmod.write_session() as db:  # (a read session rolls back on exit and would expire the returned object)
        return db.scalar(select(models.User).where(models.User.email == email))


def count(model) -> int:
    with dbmod.read_session() as db:
        return db.scalar(select(func.count()).select_from(model))


def register(client, token, email="ana@example.com", password=PASSWORD, **extra):
    return client.post("/register", data={"csrf": token, "email": email, "password": password, **extra})


def login(client, token, email="ana@example.com", password=PASSWORD, **extra):
    return client.post("/login", data={"csrf": token, "email": email, "password": password, **extra})


def outbox() -> list[Path]:
    folder = get_settings().data_dir / "outbox"
    return sorted(folder.glob("*.txt")) if folder.exists() else []


def reset_path() -> str:
    return re.search(r"http://localhost:8000(/reset/[\w-]+)", outbox()[-1].read_text(encoding="utf-8")).group(1)


def en(client):
    client.headers["accept-language"] = "en"
    return client


# ----------------------------------------------------------------------------- register
def test_register_happy_path(web):
    token = csrf(web)
    page = web.get("/register")
    assert page.status_code == 200 and 'name="email"' in page.text and 'name="invite"' not in page.text

    r = register(web, token, email="  Ana@Example.COM ")
    assert r.status_code == 303 and r.headers["location"] == "/onboarding"
    assert web.cookies.get("hc_session")

    user = get_user("ana@example.com")  # normalised to lower case, trimmed
    assert user is not None and user.role == "learner" and user.onboarded_at is None and user.level is None
    assert user.public_id.startswith("L") and verify_password(PASSWORD, user.password_hash)
    assert user.password_hash != PASSWORD
    with dbmod.read_session() as db:
        assert db.scalar(select(models.StudyPlan).where(models.StudyPlan.user_id == user.id)) is None  # plan comes in onboarding
        sub = db.scalar(select(models.Subscription).where(models.Subscription.user_id == user.id))
        assert sub.status == "trialing" and sub.provider == "trial" and sub.current_period_end > utcnow()
    # and the browser is logged in: a login-only page opens
    assert web.get("/settings").status_code == 200


def test_register_takes_language_from_the_browser(web):
    token = csrf(web)
    web.headers["accept-language"] = "zh-TW,zh;q=0.9"
    register(web, token, email="zh@example.com")
    assert get_user("zh@example.com").locale == "zh-TW"


def test_register_defaults_to_the_configured_language(web):
    token = csrf(web)
    register(web, token, email="def@example.com")
    assert get_user("def@example.com").locale == get_settings().default_locale == "vi"


@pytest.mark.parametrize("bad", ["", "not-an-email", "a@b", "a b@example.com", "a@@example.com", "@example.com",
                                 "ana@exa mple.com", "ana@.com", "x" * 250 + "@example.com", "ana@example..com"])
def test_register_rejects_invalid_emails(web, bad):
    token = csrf(en(web))
    r = register(web, token, email=bad)
    assert r.status_code == 400 and i18n.t("en", "auth.err.email_invalid") in r.text
    assert count(models.User) == 0


def test_register_duplicate_email_is_a_field_error(web):
    token = csrf(en(web))
    assert register(web, token).status_code == 303
    web.cookies.delete("hc_session")
    r = register(web, token, email="ANA@example.com", password="another long password")
    assert r.status_code == 400 and i18n.t("en", "auth.err.email_taken") in r.text and 'aria-invalid="true"' in r.text
    assert count(models.User) == 1 and verify_password(PASSWORD, get_user("ana@example.com").password_hash)


@pytest.mark.parametrize("password,key", [
    ("short", "auth.err.password_short"),
    ("1234567", "auth.err.password_short"),
    ("password", "auth.err.password_common"),
    ("PassWord123", "auth.err.password_common"),
    ("12345678", "auth.err.password_common"),
    ("aaaaaaaaaaaa", "auth.err.password_common"),
    ("ana.long@example.com", "auth.err.password_same_as_email"),
    ("Ana.Long@Example.com", "auth.err.password_same_as_email"),
    ("x" * 129, "auth.err.password_long"),
])
def test_register_rejects_weak_passwords(web, password, key):
    token = csrf(en(web))
    r = register(web, token, email="ana.long@example.com", password=password)
    assert r.status_code == 400
    assert i18n.t("en", key, min=8, max_password=128) in r.text
    assert count(models.User) == 0 and "hc_session" not in web.cookies


def test_register_respects_the_configured_minimum_length(web, monkeypatch):
    settings_env(monkeypatch, min_password_length=12)
    token = csrf(en(web))
    r = register(web, token, password="elevenchars")
    assert r.status_code == 400 and "at least 12 characters" in r.text
    assert register(web, token, password="twelve chars!").status_code == 303


def test_password_is_never_echoed_back(web):
    token = csrf(web)
    r = register(web, token, password="short")
    assert 'value="short"' not in r.text


def test_invite_code_gating(web, monkeypatch):
    settings_env(monkeypatch, invite_code="HUAYU-2026")
    token = csrf(en(web))
    assert 'name="invite"' in web.get("/register").text
    missing = register(web, token)
    wrong = register(web, token, invite="nope")
    assert missing.status_code == wrong.status_code == 400 and i18n.t("en", "auth.err.invite_invalid") in wrong.text
    assert count(models.User) == 0
    assert register(web, token, invite="  HUAYU-2026  ").status_code == 303  # stray spaces are forgiven
    assert get_user("ana@example.com") is not None


def test_a_wrong_invite_does_not_reveal_which_emails_exist(web, monkeypatch):
    add_user("taken@example.com")
    settings_env(monkeypatch, invite_code="HUAYU-2026")
    token = csrf(en(web))
    r = register(web, token, email="taken@example.com", invite="guess")
    assert i18n.t("en", "auth.err.email_taken") not in r.text and i18n.t("en", "auth.err.invite_invalid") in r.text


def test_invite_field_is_ignored_when_no_code_is_configured(web):
    token = csrf(web)
    assert register(web, token, invite="whatever").status_code == 303


def test_registration_closed(web, monkeypatch):
    settings_env(monkeypatch, registration_open="false")
    token = csrf(en(web))
    page = web.get("/register")
    assert page.status_code == 200 and i18n.t("en", "auth.register.closed_title") in page.text
    assert 'action="/register"' not in page.text
    r = register(web, token)
    assert r.status_code == 403 and count(models.User) == 0


def test_register_needs_a_valid_csrf_token(web):
    csrf(web)
    assert web.post("/register", data={"email": "a@example.com", "password": PASSWORD}).status_code == 403
    assert register(web, "forged-token").status_code == 403
    web.cookies.delete("hc_csrf")
    assert register(web, "anything").status_code == 403
    assert count(models.User) == 0


def test_register_is_rate_limited_per_ip(web):
    token = csrf(web)
    for _ in range(60):  # a class registering together shares one campus address, so the per-IP budget is generous
        assert register(web, token, email="bad").status_code == 400
    r = register(web, token, email="bad")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_logged_in_visitors_skip_register_and_login(web):
    user = add_user()
    login_as(web, user)
    for page in ("/register", "/login"):
        r = web.get(page)
        assert r.status_code == 303 and r.headers["location"] == "/onboarding"  # not onboarded yet


# ----------------------------------------------------------------------------- login / logout
def test_login_and_logout(web):
    add_user()
    token = csrf(web)
    r = login(web, token)
    assert r.status_code == 303 and r.headers["location"] == "/onboarding"  # a new learner still has to onboard
    assert web.get("/settings").status_code == 200
    assert get_user("ana@example.com").last_login_at is not None

    r = web.post("/logout", data={"csrf": web.cookies.get("hc_csrf")})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "hc_session" not in web.cookies  # the response deleted it
    r = web.get("/settings")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings"


def test_onboarded_learner_lands_on_today_and_staff_on_admin(web):
    learner = add_user("learner@example.com")
    with dbmod.write_session() as db:
        db.get(models.User, learner.id).onboarded_at = utcnow()
    add_user("boss@example.com", role="admin")
    token = csrf(web)
    assert login(web, token, "learner@example.com").headers["location"] == "/today"
    web.cookies.delete("hc_session")
    assert login(web, token, "boss@example.com").headers["location"] == "/admin"


def test_logout_needs_csrf(web):
    user = add_user()
    login_as(web, user)
    assert web.post("/logout").status_code == 403
    assert web.get("/settings").status_code == 200  # still logged in


def test_wrong_password_and_unknown_email_look_identical(web):
    add_user("ana@example.com")
    token = csrf(en(web))
    wrong = login(web, token, "ana@example.com", "not the password")
    unknown = login(web, token, "nobody@example.com", "not the password")
    assert wrong.status_code == unknown.status_code == 400
    assert i18n.t("en", "auth.err.login_failed") in wrong.text
    # same page apart from the e-mail the person typed
    assert wrong.text.replace("ana@example.com", "X") == unknown.text.replace("nobody@example.com", "X")
    assert "hc_session" not in wrong.headers.get("set-cookie", "")
    assert "hc_session" not in unknown.headers.get("set-cookie", "")
    for junk in ("", "x" * 5000):  # empty and absurd inputs get the same answer, not a 422/500
        assert login(web, token, "ana@example.com", junk).status_code == 400
    assert login(web, token, "not-an-email", "x").status_code == 400


@pytest.mark.parametrize("nxt", ["//evil.example", "https://evil.example/x", "http:evil.example", "/\\evil.example",
                                 "\\\\evil.example", "javascript:alert(1)", "evil.example", "/\t/evil.example",
                                 "/ok\r\nSet-Cookie: a=b", "/" + "a" * 400, ""])
def test_login_next_rejects_open_redirects(web, nxt):
    add_user()
    with dbmod.write_session() as db:
        db.scalar(select(models.User)).onboarded_at = utcnow()
    token = csrf(web)
    assert 'name="next" value=""' in web.get("/login", params={"next": nxt}).text
    r = login(web, token, next=nxt)
    assert r.status_code == 303 and r.headers["location"] == "/today"


def test_login_next_accepts_local_paths(web):
    add_user()
    with dbmod.write_session() as db:
        db.scalar(select(models.User)).onboarded_at = utcnow()
    token = csrf(web)
    page = web.get("/login", params={"next": "/progress?tab=1"})
    assert 'name="next" value="/progress?tab=1"' in page.text
    r = login(web, token, next="/progress?tab=1")
    assert r.status_code == 303 and r.headers["location"] == "/progress?tab=1"


def test_login_is_rate_limited_per_email_and_blocks_even_the_right_password(web):
    add_user()
    token = csrf(web)
    for _ in range(8):
        assert login(web, token, password="wrong password").status_code == 400
    r = login(web, token, password="wrong password")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert login(web, token).status_code == 429  # a guesser who got it right still waits
    assert login(web, token, "other@example.com", "x").status_code == 400  # other e-mails are not locked out
    limiter.reset()
    assert login(web, token).status_code == 303


def test_login_needs_csrf(web):
    add_user()
    csrf(web)
    assert web.post("/login", data={"email": "ana@example.com", "password": PASSWORD}).status_code == 403
    assert "hc_session" not in web.cookies


def test_login_upgrades_an_old_password_hash(web):
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    user = add_user()
    with dbmod.write_session() as db:
        db.get(models.User, user.id).password_hash = weak
    token = csrf(web)
    assert login(web, token).status_code == 303
    new_hash = get_user("ana@example.com").password_hash
    assert new_hash != weak and verify_password(PASSWORD, new_hash)


# ----------------------------------------------------------------------------- forgotten password
def test_forgot_gives_the_same_answer_for_known_and_unknown_addresses(web):
    add_user("known@example.com")
    token = csrf(web)
    a = web.post("/forgot", data={"csrf": token, "email": "known@example.com"})
    b = web.post("/forgot", data={"csrf": token, "email": "unknown@example.com"})
    assert a.status_code == b.status_code == 303 and a.headers["location"] == b.headers["location"] == "/login"
    assert a.headers["set-cookie"] == b.headers["set-cookie"]  # same flash, byte for byte
    assert len(outbox()) == 1 and "To: known@example.com" in outbox()[0].read_text(encoding="utf-8")
    with dbmod.read_session() as db:
        assert db.scalar(select(func.count()).select_from(models.PasswordReset)) == 1
    follow = web.get("/login")
    assert i18n.t("vi", "auth.flash.reset_sent") in follow.text


def test_forgot_rejects_a_malformed_address_without_sending_anything(web):
    token = csrf(en(web))
    r = web.post("/forgot", data={"csrf": token, "email": "nonsense"})
    assert r.status_code == 400 and i18n.t("en", "auth.err.email_invalid") in r.text and not outbox()


def test_forgot_needs_csrf(web):
    add_user()
    csrf(web)
    assert web.post("/forgot", data={"email": "ana@example.com"}).status_code == 403
    assert not outbox()


def test_forgot_is_rate_limited_for_known_and_unknown_addresses_alike(web):
    add_user("known@example.com")
    token = csrf(web)
    for addr in ("known@example.com", "nobody@example.com"):
        for _ in range(3):
            assert web.post("/forgot", data={"csrf": token, "email": addr}).status_code == 303
        # beyond the cap the answer is IDENTICAL (neutral 303): a stranger can neither tell whether the address exists
        # nor lock the owner out of recovery with a 429; the extra requests simply send nothing
        assert web.post("/forgot", data={"csrf": token, "email": addr}).status_code == 303
    assert len(outbox()) == 3


def test_password_reset_end_to_end(web):
    user = add_user("ana@example.com")
    with dbmod.write_session() as db:
        db.get(models.User, user.id).locale = "vi"
    other_device = web.__class__(web.app, follow_redirects=False)
    login_as(other_device, user)  # a session that existed before the reset
    assert other_device.get("/settings").status_code == 200

    token = csrf(web)
    web.post("/forgot", data={"csrf": token, "email": "ana@example.com"})
    mail = outbox()[-1].read_text(encoding="utf-8")
    assert "Subject: " + i18n.t("vi", "auth.mail.reset_subject") in mail and "Xin chào" in mail  # in the learner's language
    path = reset_path()

    page = web.get(path)
    assert page.status_code == 200 and f'action="{path}"' in page.text
    token = web.cookies.get("hc_csrf")
    weak = web.post(path, data={"csrf": token, "password": "password", "password2": "password"})
    mismatch = web.post(path, data={"csrf": token, "password": "a brand new secret", "password2": "a brand new secreT"})
    assert weak.status_code == mismatch.status_code == 400 and 'action="' + path + '"' in weak.text  # form stays usable
    assert verify_password(PASSWORD, get_user("ana@example.com").password_hash)

    ok = web.post(path, data={"csrf": token, "password": "a brand new secret", "password2": "a brand new secret"})
    assert ok.status_code == 303 and ok.headers["location"] == "/login"
    new_hash = get_user("ana@example.com").password_hash
    assert verify_password("a brand new secret", new_hash) and not verify_password(PASSWORD, new_hash)

    # the link is single-use
    assert web.get(path).status_code == 400
    again = web.post(path, data={"csrf": token, "password": "yet another secret", "password2": "yet another secret"})
    assert again.status_code == 400 and verify_password("a brand new secret", get_user("ana@example.com").password_hash)

    # the old session died, the new password works
    r = other_device.get("/settings")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert login(web, token, password="a brand new secret").status_code == 303
    assert get_user("ana@example.com").session_version == 2


def test_expired_used_and_forged_tokens_are_rejected(web):
    user = add_user()
    raw_expired, raw_used = "e" * 43, "u" * 43
    with dbmod.write_session() as db:
        db.add(models.PasswordReset(user_id=user.id, token_hash=hash_token(raw_expired), expires_at=utcnow() - timedelta(minutes=1)))
        db.add(models.PasswordReset(user_id=user.id, token_hash=hash_token(raw_used), expires_at=utcnow() + timedelta(hours=1),
                                    used_at=utcnow()))
    csrf(en(web))
    token = web.cookies.get("hc_csrf")
    for bad in (raw_expired, raw_used, "f" * 43, "x", "../../etc/passwd", "a" * 500):
        assert web.get(f"/reset/{bad}").status_code in (400, 404), bad
        r = web.post(f"/reset/{bad}", data={"csrf": token, "password": "a brand new secret", "password2": "a brand new secret"})
        assert r.status_code in (400, 404), bad
    r = web.get(f"/reset/{raw_expired}")
    assert i18n.t("en", "auth.reset.invalid_title") in r.text and 'name="password"' not in r.text
    assert verify_password(PASSWORD, get_user("ana@example.com").password_hash)


def test_a_newer_reset_link_replaces_the_older_one(web):
    add_user()
    token = csrf(web)
    web.post("/forgot", data={"csrf": token, "email": "ana@example.com"})
    first = reset_path()
    web.post("/forgot", data={"csrf": token, "email": "ana@example.com"})
    second = reset_path()
    # a stranger asking for links must not be able to invalidate the owner's link: earlier links stay valid...
    assert first != second and web.get(first).status_code == 200 and web.get(second).status_code == 200
    # ...but at most three live links exist at once, the oldest are retired
    for _ in range(2):
        web.post("/forgot", data={"csrf": token, "email": "ana@example.com"})
        limiter.reset()
    assert web.get(first).status_code == 400 and web.get(reset_path()).status_code == 200


def test_reset_needs_csrf(web):
    add_user()
    token = csrf(web)
    web.post("/forgot", data={"csrf": token, "email": "ana@example.com"})
    path = reset_path()
    r = web.post(path, data={"password": "a brand new secret", "password2": "a brand new secret"})
    assert r.status_code == 403 and verify_password(PASSWORD, get_user("ana@example.com").password_hash)


def test_reset_is_rate_limited(web):
    csrf(web)
    for _ in range(20):
        web.get("/reset/" + "z" * 43)
    assert web.get("/reset/" + "z" * 43).status_code == 429


# ----------------------------------------------------------------------------- pages: no raw keys, three languages
def _namespace_keys() -> list[str]:
    return [k for k in i18n.catalog("en") if k.startswith(("auth.", "account."))]


@pytest.mark.parametrize("locale", LOCALES)
def test_auth_pages_show_translated_text_never_raw_keys(web, monkeypatch, locale):
    add_user("known@example.com")
    web.cookies.set("hc_lang", locale)
    token = csrf(web)
    pages = [web.get("/login"), web.get("/register"), web.get("/forgot"), web.get("/reset/" + "q" * 43),
             login(web, token, "known@example.com", "wrong password"),
             register(web, token, email="bad", password="x"),
             register(web, token, email="known@example.com", password="password"),
             web.post("/forgot", data={"csrf": token, "email": "bad"})]
    settings_env(monkeypatch, invite_code="abc", billing_enabled="true")
    pages.append(web.get("/register"))
    pages.append(register(web, token, invite="wrong"))
    settings_env(monkeypatch, registration_open="false")
    pages.append(web.get("/register"))
    for page in pages:
        assert page.status_code in (200, 400, 403), page.url
        assert f'<html lang="{locale}">' in page.text
        for key in _namespace_keys():
            assert key not in page.text, (locale, page.url, key)
        assert "{min}" not in page.text and "{max_password}" not in page.text and "{minutes}" not in page.text
    sample = {"vi": "Đăng nhập", "zh-TW": "登入", "en": "Log in"}[locale]
    assert sample in pages[0].text


def test_every_translation_key_used_by_templates_and_routers_exists_in_all_locales():
    wanted: set[str] = set()
    for path in [*(ROOT / "app/templates/auth").glob("*.html"), *(ROOT / "app/templates/account").glob("*.html"),
                 ROOT / "app/routers/auth.py", ROOT / "app/routers/account.py"]:
        text = path.read_text(encoding="utf-8")
        wanted |= set(re.findall(r"""['"]((?:auth|account|common)\.[A-Za-z0-9_.]*[A-Za-z0-9_])['"]""", text))
        # prefixes built at render time: t('account.tz.' ~ key), t('account.mode.' ~ m ~ '.label')
        for prefix in re.findall(r"""t\('((?:auth|account)\.[A-Za-z0-9_.]*\.)' ~""", text):
            assert any(k.startswith(prefix) for k in i18n.catalog("en")), prefix
    assert len(wanted) > 40
    for loc in LOCALES:
        missing = sorted(k for k in wanted if k not in i18n.catalog(loc))
        assert not missing, (loc, missing)


def test_zh_tw_strings_use_traditional_characters_and_taiwan_terms():
    simplified = set("账码设们户个载邮验认录页项选务与为这还请进开关机应转换览隐协议帐")
    mainland_terms = ("用戶", "軟件", "信息", "網絡", "賬號", "郵箱")
    for name in ("auth", "account"):
        text = (ROOT / "app/locales/zh-TW" / f"{name}.json").read_text(encoding="utf-8")
        assert not (simplified & set(text)), simplified & set(text)
        assert not [t for t in mainland_terms if t in text]
