"""Public website (landing, pricing, privacy, terms, manifest, robots) and the deploy kit files."""
from __future__ import annotations

import html
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES, ROOT, Settings, get_settings
from tests.conftest import login_as, make_user, onboarded_user

APP = ROOT / "app"
STATIC = APP / "static"
TEMPLATES = APP / "templates" / "marketing"
PAGES = ("/", "/pricing", "/privacy", "/terms")
KEY_RE = re.compile(r"marketing\.(?:meta|manifest|home|pricing|legal|privacy|terms)\.[a-z0-9_]+")


@pytest.fixture(autouse=True)
def _marketing_only(monkeypatch):
    """Mount only this slice's router and pin every setting the pages depend on (a developer's .env must not leak in)."""
    monkeypatch.setenv("HUAYU_ROUTERS", "marketing")
    for name, value in {"BILLING_ENABLED": "false", "TRIAL_DAYS": "14", "SUPPORT_EMAIL": "", "LLM_PROVIDER": "none",
                        "ALLOW_UNREVIEWED_QUESTIONS": "true", "DEFAULT_LOCALE": "vi",
                        "BASE_URL": "http://localhost:8000"}.items():
        monkeypatch.setenv(f"HUAYU_{name}", value)
    get_settings.cache_clear()


def configure(monkeypatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(f"HUAYU_{name.upper()}", str(value))
    get_settings.cache_clear()


def page(client, path: str, locale: str = "en"):
    return client.get(path, headers={"accept-language": locale})


def text_of(response) -> str:
    """Visible text of a rendered page (tags stripped, entities decoded)."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", response.text, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


# ----------------------------------------------------------------------------- rendering in all three locales
@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("path", PAGES)
def test_pages_render_without_raw_keys_in_every_locale(web, seeded, locale, path):
    r = page(web, path, locale)
    assert r.status_code == 200 and f'<html lang="{locale}">' in r.text
    assert not KEY_RE.findall(r.text), KEY_RE.findall(r.text)
    for key in i18n.catalog("en"):
        if key.startswith("marketing."):
            assert key not in r.text, key
    assert not re.search(r"{(days|price|email)}", r.text), "an unfilled {placeholder} reached the page"
    assert "<h1" in r.text and "canonical" in r.text


def _used_keys() -> set[str]:
    """Every marketing.* key the templates and the router reference, dynamic ones expanded."""
    used: set[str] = set()
    stems = {"marketing.home.step", "marketing.home.reason_"}  # built with `~` in home.html, expanded below
    sources = [p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")] + [(APP / "routers" / "marketing.py").read_text()]
    for src in sources:
        used |= set(re.findall(r"'(marketing\.[a-z]+\.[a-z0-9_]+)'", src)) | set(re.findall(r'"(marketing\.[a-z]+\.[a-z0-9_]+)"', src))
        for prefix, count in re.findall(r"items\('(marketing\.[a-z]+\.[a-z0-9_]+)', (\d+)", src):
            stems.add(prefix)
            used |= {f"{prefix}{i}" for i in range(1, int(count) + 1)}
    used -= stems  # a stem is not a key by itself
    used |= {f"marketing.home.step{n}_{part}" for n in range(1, 5) for part in ("title", "body")}
    used |= {f"marketing.home.reason_{c}_{part}" for c in ("too_hard", "no_time", "need_rest", "distracted") for part in ("title", "body")}
    return used


def test_every_template_key_exists_and_no_translation_is_dead():
    catalog = {k for k in i18n.catalog("en") if k.startswith("marketing.")}
    used = _used_keys()
    assert not used - catalog, f"used but not translated: {sorted(used - catalog)}"
    assert not catalog - used, f"translated but never used: {sorted(catalog - used)}"


def test_zh_tw_text_is_traditional_not_simplified():
    simplified = set("这个们说学习计划为与务资码户设录隐条删导电邮时间开关试费订阅进总结书网页")
    found = simplified & set((APP / "locales" / "zh-TW" / "marketing.json").read_text(encoding="utf-8"))
    assert not found, f"simplified characters in zh-TW: {''.join(sorted(found))}"


def test_pages_use_the_visitors_language(web, seeded):
    assert "Học tiếng Hoa không áp lực" in page(web, "/", "vi").text
    assert "沒有壓力的華語學習" in page(web, "/", "zh-TW").text
    assert "Learn Mandarin without pressure" in page(web, "/", "en").text
    web.cookies.set("hc_lang", "en")  # an explicit choice beats Accept-Language
    assert "Learn Mandarin without pressure" in page(web, "/", "vi").text


# ----------------------------------------------------------------------------- landing page
def test_landing_redirects_logged_in_users_home(web, engine):
    with dbmod.write_session() as s:
        learner, teacher, admin = onboarded_user(s), make_user(s, role="teacher"), make_user(s, role="admin")
    assert web.get("/").status_code == 200  # anonymous
    for user, target in ((learner, "/today"), (teacher, "/admin"), (admin, "/admin")):
        login_as(web, user)
        r = web.get("/")
        assert r.status_code == 303 and r.headers["location"] == target
    web.cookies.set("hc_session", "forged.token")  # an invalid session is just an anonymous visitor
    assert web.get("/").status_code == 200


def test_landing_page_content_is_honest(web, seeded):
    en = text_of(page(web, "/", "en"))
    for needed in ("Create an account", "Nothing changes without your OK", "Rules decide every number", "A1–A2",
                   "too hard", "no time", "need a rest", "distracted", "Free during the pilot"):
        assert needed.lower() in en.lower(), needed
    assert "We make no promise about exam results" in en
    assert not re.search(r"\d+\s*%|testimonial", en, re.I), "no invented statistics or testimonials"
    assert 'href="/register"' in page(web, "/", "en").text and 'href="/pricing"' in page(web, "/", "en").text


def test_landing_page_follows_the_billing_and_review_settings(web, seeded, monkeypatch):
    en = text_of(page(web, "/", "en"))
    assert "Free during the pilot" in en and "Some of the questions you will see have not been reviewed yet" in en
    configure(monkeypatch, billing_enabled="true", trial_days="21", allow_unreviewed_questions="false")
    en = text_of(page(web, "/", "en"))
    assert "21-day free trial" in en and "Free during the pilot" not in en
    assert "only approved questions are shown" in en and "have not been reviewed yet" not in en


def test_chinese_learning_text_is_marked_up_as_chinese(web):
    assert '<span class="zh" lang="zh-TW">慢慢來，比較快。</span>' in page(web, "/", "vi").text


# ----------------------------------------------------------------------------- pricing
def test_pricing_shows_database_prices_and_the_free_pilot(web, seeded):
    r = page(web, "/pricing", "en")
    t = text_of(r)
    assert "Free during the pilot" in t and "Planned prices after the pilot" in t and "free trial" not in t
    assert "NT$ 199" in t and "NT$ 799" in t and "Monthly" in t and "30 days of access" in t and "150 days of access" in t
    assert t.index("NT$ 199") < t.index("NT$ 799")  # cheapest first
    vi, zh = text_of(page(web, "/pricing", "vi")), text_of(page(web, "/pricing", "zh-TW"))
    assert "Theo tháng" in vi and "NT$ 199" in vi and "Miễn phí trong giai đoạn thử nghiệm" in vi
    assert "月方案" in zh and "NT$ 799" in zh and "試辦期間免費" in zh and "試用期間" not in zh  # pilot = 試辦; 試用 is only the 14-day trial


def test_pricing_reads_the_database_not_a_constant(web, engine):
    with dbmod.write_session() as s:
        s.add_all([
            models.BillingPlan(code="yearly", name={"en": "Yearly"}, price_twd=1999, period_days=365),
            models.BillingPlan(code="old", name={"en": "Retired"}, price_twd=5, period_days=1, active=False),
        ])
    r = page(web, "/pricing", "vi")
    t = text_of(r)
    assert "NT$ 1,999" in t and "365 ngày sử dụng" in t
    assert "Yearly" in t, "a plan without a Vietnamese name falls back to English"
    assert 'data-plan="old"' not in r.text and "Retired" not in t


def test_pricing_without_plans_and_with_billing_on(web, engine, monkeypatch):
    assert "Prices will be published soon" in text_of(page(web, "/pricing"))
    with dbmod.write_session() as s:
        s.add(models.BillingPlan(code="monthly", name={"en": "Monthly"}, price_twd=250, period_days=30))
    configure(monkeypatch, billing_enabled="true", trial_days="21")
    t = text_of(page(web, "/pricing"))
    assert "21-day free trial" in t and "NT$ 250" in t and "Free during the pilot" not in t and "Planned prices" not in t
    assert "no automatic renewal" in t.lower() and "handled by hand" in t


# ----------------------------------------------------------------------------- privacy + terms
PRIVACY_TERMS = {
    "en": ["e-mail address", "password hash", "study answers", "Interruptions", "note you write", "plan history", "Ratings",
           "Consent records", "Subscription and payment", "SQLite", "Personal Data Protection Act", "download a copy",
           "delete your account", "Rest button", "separate consent", "never sent", "Cookies", "14 daily copies"],
    "vi": ["địa chỉ email", "mã băm của mật khẩu", "Câu trả lời khi học", "Lần dừng học", "ghi chú tùy chọn", "lịch sử kế hoạch",
           "Đánh giá", "Bản ghi đồng ý", "gói đăng ký và thanh toán", "SQLite", "個人資料保護法", "tải về một bản sao",
           "xóa tài khoản", "nút Nghỉ", "đồng ý riêng", "không bao giờ được gửi", "Cookie", "14 bản sao lưu"],
    "zh-TW": ["電子郵件", "密碼的雜湊值", "作答紀錄", "中斷紀錄", "選填的備註", "計畫紀錄", "評分", "同意紀錄", "訂閱與付款紀錄",
              "SQLite", "個人資料保護法", "下載一份你的資料", "刪除帳號", "「休息」按鈕", "另外同意", "絕不會傳送", "Cookie",
              "14 天的每日備份"],
}


@pytest.mark.parametrize("locale", LOCALES)
def test_privacy_policy_covers_everything_that_is_stored(web, locale):
    r = page(web, "/privacy", locale)
    t = text_of(r)
    missing = [term for term in PRIVACY_TERMS[locale] if term not in t]
    assert not missing, missing
    assert t.count(i18n.t(locale, "marketing.legal.draft")) == 1  # the draft banner is on the page
    assert r.text.count('class="placeholder"') == 4  # operator name, address, tax id and (no HUAYU_SUPPORT_EMAIL here) the contact e-mail
    assert i18n.t(locale, "marketing.legal.operator_name") in t


def test_privacy_policy_matches_the_models():
    """Tripwire: if a table is added or renamed, the privacy page (what we store) must be revisited."""
    tables = sorted(t.name for t in models.Base.metadata.sorted_tables)
    assert tables == ["audit_log", "billing_plans", "consents", "feedback", "interruptions", "password_resets",
                              "payments", "plan_changes", "questions", "quiz_attempts", "recommendations", "study_plans",
                              "study_tasks", "subscriptions", "suggestion_items", "users"], (
        "the data model changed: update marketing privacy.s1_* (and s7_p2) in all three locales, then update this list")


def test_privacy_policy_states_the_live_ai_setting(web, monkeypatch):
    assert "AI wording is switched off" in text_of(page(web, "/privacy"))
    configure(monkeypatch, llm_provider="openai", llm_api_key="test-key", llm_model="test-model")
    on = text_of(page(web, "/privacy"))
    assert "AI wording is switched on" in on and "switched off" not in on


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_support_email_line_only_when_configured(web, monkeypatch, path):
    r = page(web, path)
    assert 'id="contact"' not in r.text and 'id="contact-missing"' in r.text  # no e-mail: the gap is shown as a yellow blank
    assert i18n.t("en", "marketing.legal.contact_placeholder") in text_of(r)
    configure(monkeypatch, support_email="help@example.org")
    r = page(web, path)
    assert 'id="contact"' in r.text and "help@example.org" in text_of(r) and 'id="contact-missing"' not in r.text
    assert r.text.count('class="placeholder"') == (3 if path == "/privacy" else 5)


@pytest.mark.parametrize("locale", LOCALES)
def test_terms_are_marked_draft_and_name_no_invented_company(web, locale):
    r = page(web, "/terms", locale)
    t = text_of(r)
    assert i18n.t(locale, "marketing.legal.draft") in t
    assert r.text.count('class="placeholder"') == 6  # operator x3, refund policy, court, and the missing contact e-mail
    assert i18n.t(locale, "marketing.legal.refund_placeholder") in t


def test_terms_follow_the_billing_setting(web, monkeypatch):
    t = text_of(page(web, "/terms"))
    assert "While the pilot runs, the service is free" in t and "free trial" not in t
    configure(monkeypatch, billing_enabled="true", trial_days="30")
    t = text_of(page(web, "/terms"))
    assert "30-day free trial" in t and "While the pilot runs" not in t
    assert "There is no automatic renewal" in t and "Taiwan (Republic of China)" in t


def test_legal_pages_escape_everything(web, monkeypatch):
    configure(monkeypatch, support_email="a@b.example<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in page(web, "/privacy").text


# ----------------------------------------------------------------------------- honesty of the public copy (review: honesty lens)
def i18n_all(key: str) -> dict[str, str]:
    return {loc: i18n.t(loc, key) for loc in LOCALES}


def test_privacy_page_describes_deletion_backups_logs_and_mail_as_they_really_are(web):
    en = text_of(page(web, "/privacy", "en"))
    for needed in (
        "detached from you and with their note removed",          # payment rows kept for bookkeeping
        "minimal entry for each action an administrator took",    # the admin log after a deletion
        "records that an account was deleted, without the code",
        "any note an administrator typed is removed",
        "old bytes inside the SQLite file until new data overwrites them",
        "can remain in a backup for up to 14 days",
        "can come back until it is deleted again",                # a restore resurrects an account deleted after the backup
        "off the server are the operator's responsibility",
        "no line per visit", "rotated by size", "no IP address",
        "Each link works once", "mail server", "not an anonymous one",
        "Teachers see the question bank only",
        "the entries about your account are in your data export",  # the export has "staff_actions_about_you" (account router)
    ):
        assert needed in en, needed
    assert "anonymous learner code" not in en
    for loc, phrase in {"vi": "mã người học ẩn danh", "zh-TW": "匿名的學習者代碼"}.items():
        assert phrase not in text_of(page(web, "/privacy", loc)), loc


@pytest.mark.parametrize("loc", LOCALES)
def test_privacy_and_terms_keep_promises_that_an_operator_can_keep(web, loc):
    """F10/F12: changes are announced by e-mail (there is no in-app notice feature); backups: 14 days, off-site copies are the operator's."""
    for key in ("marketing.pricing.planned_note", "marketing.terms.s4_pilot", "marketing.terms.s9_p1", "marketing.privacy.s9_p1"):
        text = i18n.t(loc, key)
        assert any(word in text.lower() for word in ("e-mail", "email", "電子郵件")), (loc, key)
        assert not any(p in text.lower() for p in ("in the app", "trong ứng dụng", "應用程式內", "應用程式中")), (loc, key)
    for key in ("marketing.privacy.s2_p2", "marketing.privacy.s7_p3"):
        assert "14" in i18n.t(loc, key), (loc, key)


def test_ai_wording_is_described_as_optional_off_by_default_and_not_as_a_hard_guarantee(web):
    en = text_of(page(web, "/", "en"))
    assert "optional and off by default" in en and "chosen by the operator" in en
    assert "only chooses words" not in en and "cannot add, remove or change a suggestion" not in en
    privacy = text_of(page(web, "/privacy", "en"))
    assert "the list is what counts" in privacy and "cannot check the rest of the wording" in privacy
    ui = i18n.t("en", "coach.proposal.ai_note")
    assert "only rephrases" not in ui and "the list below" in ui
    # the onboarding / settings wording names the recipient category, says what is sent and that it only applies if the operator enables AI
    assert "AI provider chosen by the operator" in i18n.t("en", "account.onboarding.consent_llm")
    assert "AI provider chosen by the operator" in i18n.t("en", "account.settings.ai_on")
    assert "if the operator turns on AI wording" in i18n.t("en", "account.onboarding.consent_llm")
    assert "nothing is sent" in i18n.t("en", "account.onboarding.llm_inactive")


def test_home_page_claims_match_what_the_engine_does(web):
    en = text_of(page(web, "/", "en"))
    assert "No suggestion adds study time" in en and "never up" not in en           # F07(c): a learner may still set any number
    assert "vocabulary, grammar and reading" not in en                              # F01: the daily bank has no reading items yet
    assert "question bank is still growing" in en
    assert "being reviewed by teachers step by step" in text_of(page(web, "/terms", "en"))   # F15(a), same wording as vi / zh-TW
    assert "Practice questions are reviewed by teachers step by step" not in en
    assert "answer options and explanations are in Vietnamese or Chinese" in en     # F03: say so on the public page
    assert "Export and deletion of your data" not in text_of(page(web, "/pricing", "en"))  # F15(c): a right, not a plan feature


def test_zh_tw_uses_one_word_per_idea(web):
    marketing = (APP / "locales" / "zh-TW" / "marketing.json").read_text(encoding="utf-8")
    assert "試用期間" not in marketing and "試用期免費" not in marketing and "試用期結束" not in marketing   # pilot = 試辦
    assert "試辦期間免費" in marketing and "免費試用 {days} 天" in marketing                                  # trial = 試用
    assert "新文法" not in marketing and "新句型" in marketing                                              # 句型 for the numbers, 文法 only as the skill
    assert "台灣" not in (APP / "locales" / "zh-TW" / "account.json").read_text(encoding="utf-8")        # 臺灣 everywhere
    vi = " ".join((APP / "locales" / "vi" / f"{n}.json").read_text(encoding="utf-8") for n in ("marketing", "auth", "account"))
    assert "tiếng Trung" not in vi and "tick" not in (APP / "locales" / "vi" / "coach.json").read_text(encoding="utf-8")
    assert "Mình nên gọi bạn" not in vi                                                                    # "mình" means the learner, never the app


# ----------------------------------------------------------------------------- accessibility of the shared frame and public pages
def test_chinese_and_vietnamese_names_inside_other_languages_carry_a_lang_attribute(web):
    privacy = page(web, "/privacy", "en").text
    assert '<span lang="zh-TW">個人資料保護法</span>' in privacy and '<span lang="zh-TW">統一編號</span>' in privacy
    home = page(web, "/", "en").text
    assert '<span lang="vi">Tiếng Việt</span>' in home and '<span lang="zh-TW">繁體中文</span>' in home
    assert '<span class="brand-zh" lang="zh-TW">華語教練</span>' in home
    assert "&lt;" not in privacy.split("個人資料保護法")[0][-200:], "the helper must not double-escape"


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("path", PAGES)
def test_public_pages_have_one_h1_and_no_dangling_aria_references(web, locale, path):
    html = page(web, path, locale).text
    assert html.count("<h1") == 1
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    for attr in ("aria-labelledby", "aria-describedby"):
        for ref in re.findall(attr + r'="([^"]+)"', html):
            assert set(ref.split()) <= ids, (path, attr, ref)
    assert '<summary aria-label' not in html, "the visible text of the language button must be part of its accessible name"


def test_header_dom_order_is_brand_nav_tools_and_css_does_not_reorder_it(web, engine):
    with dbmod.write_session() as s:
        learner = onboarded_user(s)
    login_as(web, learner)
    html = page(web, "/privacy", "en").text
    assert html.index('class="brand"') < html.index('class="mainnav"') < html.index('class="tools"')
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"\.mainnav[^{}]*{[^}]*\border\s*:", css), "visual order must equal DOM order (WCAG 2.4.3)"
    assert not re.search(r"\.tools[^{}]*{[^}]*\border\s*:", css)


# ----------------------------------------------------------------------------- colour contrast (WCAG 2.x), computed from the real tokens
def _channel(v: int) -> float:
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_palette_meets_wcag_aa_text_and_non_text_contrast():
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    c = {name: value for name, value in re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", root)}
    warn_text = re.search(r"\.badge\.warn \{[^}]*color: (#[0-9a-fA-F]{6})", css).group(1)
    c["warn-text"] = warn_text
    text_pairs = [("text", "bg"), ("text", "card"), ("text", "teal-soft"), ("muted", "bg"), ("muted", "card"), ("muted", "teal-soft"),
                  ("muted", "orange-soft"), ("teal-dark", "bg"), ("teal-dark", "card"), ("teal-dark", "teal-soft"),
                  ("teal-dark", "orange-soft"), ("card", "teal"), ("navy", "orange"), ("card", "red"), ("card", "navy"),
                  ("red", "card"), ("red", "bg"), ("red", "red-soft"), ("green", "green-soft"), ("warn-text", "orange-soft")]
    for fg, bg in text_pairs:
        assert contrast(c[fg], c[bg]) >= 4.5, (fg, bg, round(contrast(c[fg], c[bg]), 2))
    ui_pairs = [("ctl", "card"), ("ctl", "bg"), ("ctl", "teal-soft"), ("ctl", "orange-soft"),     # borders of inputs, options, choices, chips
                ("navy", "card"), ("navy", "bg"), ("navy", "teal-soft"), ("navy", "orange-soft"),  # focus ring on the page
                ("card", "navy"),                                                                # the white gap of the focus ring on the header
                ("teal", "teal-soft"), ("green", "green-soft"), ("red", "red-soft"), ("faint", "card")]
    for fg, bg in ui_pairs:
        assert contrast(c[fg], c[bg]) >= 3.0, (fg, bg, round(contrast(c[fg], c[bg]), 2))
    # the old failures must stay gone: orange focus ring (2.1:1) and #b9c7d4 / #dbe4ec borders on controls (1.7:1 / 1.3:1)
    assert ":focus-visible { outline: 3px solid var(--navy)" in css and "var(--orange)" not in re.findall(r":focus-visible[^}]*}", css)[0]
    assert "#b9c7d4" not in css and "border: 2px solid var(--line)" not in css.split("/* learning */", 1)[1].split("/* tables", 1)[0].replace(
        ".sample-items", "")
    for sheet in (STATIC / "css").glob("*.css"):
        text = sheet.read_text(encoding="utf-8")
        assert not re.search(r"[^-]color:\s*var\(--orange\)", text), f"{sheet.name}: orange must never carry text"
        assert not re.search(r"[^-]color:\s*var\(--teal\)", text), f"{sheet.name}: small teal text uses --teal-dark (4.28:1 on teal-soft)"
    assert round(contrast("#f59d52", "#ffffff"), 2) < 3.0, "sanity: this is why orange is not the focus ring"


# ----------------------------------------------------------------------------- PWA manifest, icons, robots
def read_png(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, chunks = 8, []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc, f"bad CRC in {kind!r}"
        chunks.append((kind, body))
        pos += 12 + length
    assert chunks[0][0] == b"IHDR" and chunks[-1][0] == b"IEND"
    width, height, depth, color_type = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (depth, color_type) == (8, 6)  # 8-bit RGBA
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    assert len(raw) == height * (1 + width * 4)
    return width, height, raw


def test_manifest_is_valid_and_complete(web):
    r = web.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.text)
    assert m["name"] == "華語教練 Huayu Coach" and m["short_name"] and m["description"]
    assert m["start_url"] == "/today" and m["display"] == "standalone" and m["scope"] == "/"
    assert m["theme_color"] == "#14233c" and m["background_color"] == "#f6f9fb"
    assert m["lang"] == "vi"  # the default locale
    sizes = {}
    for icon in m["icons"]:
        assert icon["src"].startswith("/static/") and (STATIC / icon["src"].removeprefix("/static/")).is_file(), icon
        if icon["type"] == "image/png":
            w, h, _ = read_png(STATIC / icon["src"].removeprefix("/static/"))
            assert icon["sizes"] == f"{w}x{h}", icon
            sizes[(w, icon["purpose"])] = True
    assert (192, "any") in sizes and (512, "any") in sizes and (512, "maskable") in sizes
    assert any(i["type"] == "image/svg+xml" for i in m["icons"])
    assert web.get("/static/manifest/icon-192.png").status_code == 200  # actually served


def test_manifest_icons_are_drawn_and_the_maskable_one_is_full_bleed():
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        w, h, raw = read_png(STATIC / "manifest" / name)
        colours = {raw[i + 1:i + 5] for r in range(h) for i in (r * (1 + w * 4) + 4 * (w // 2),)}  # centre column
        assert w == h and len(colours) >= 2, f"{name} looks blank"
    navy = bytes((0x14, 0x23, 0x3C, 255))
    assert read_png(STATIC / "manifest" / "icon-maskable-512.png")[2][1:5] == navy, "maskable icon needs an opaque corner"
    assert read_png(STATIC / "manifest" / "icon-512.png")[2][4] == 0, "the rounded icon has a transparent corner"


def test_base_template_links_the_manifest(web):
    assert 'rel="manifest" href="/manifest.webmanifest"' in page(web, "/").text


def test_robots_txt(web):
    r = web.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert lines[:2] == ["User-agent: *", "Allow: /"]
    for path in ("/admin", "/settings", "/today", "/task", "/placement", "/posttest", "/stuck", "/proposal", "/plan",
                 "/progress", "/billing"):
        assert f"Disallow: {path}" in lines, path
    disallowed = [ln.split(": ")[1] for ln in lines if ln.startswith("Disallow:")]
    for public in ("/", "/pricing", "/privacy", "/terms", "/register", "/login", "/manifest.webmanifest"):
        assert not any(public.startswith(prefix) for prefix in disallowed), f"{public} must stay crawlable"


def test_marketing_routes_change_no_state_and_reject_posts(web):
    for path in PAGES + ("/manifest.webmanifest", "/robots.txt"):
        assert web.post(path).status_code in (404, 405, 403)


# ----------------------------------------------------------------------------- deploy kit
DEPLOY = ROOT / "deploy"
HAS_BASH = shutil.which("bash") is not None and shutil.which("gzip") is not None


def test_dockerfile_is_conventional_and_its_inputs_exist():
    d = (ROOT / "Dockerfile").read_text()
    for needed in ("FROM python:3.13-slim", "uv sync --frozen --no-dev", "USER huayu", "HEALTHCHECK", "VOLUME /data",
                   "HUAYU_DATA_DIR=/data", "HUAYU_ENV=prod", "HUAYU_TRUSTED_PROXY=true", "python -m app.cli prepare",
                   "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers", "/healthz"):
        assert needed in d, needed
    assert "--no-access-log" in d, "the access log would write /reset/<token> links"
    assert d.index("USER huayu") < d.index("HEALTHCHECK") < d.index("CMD [")
    for line in d.splitlines():
        if line.startswith("COPY ") and "--from" not in line:
            for src in line.split()[1:-1]:
                assert (ROOT / src).exists(), f"Dockerfile copies a missing path: {src}"


def test_dockerignore_keeps_secrets_and_local_data_out_of_the_image():
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for needed in (".venv", ".env", "data", "*.db"):
        assert needed in ignored, needed


def test_compose_file_parses_and_has_the_right_shape():
    text = (ROOT / "compose.yml").read_text()
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
    cfg = yaml.safe_load(text)
    app, caddy = cfg["services"]["app"], cfg["services"]["caddy"]
    assert app["build"] == "." and app["env_file"] == ".env" and app["restart"] == "unless-stopped"
    assert "ports" not in app, "the app must only be reachable through Caddy"
    assert "huayu-data:/data" in app["volumes"]
    assert app["environment"]["HUAYU_ENV"] == "prod" and app["environment"]["HUAYU_DATA_DIR"] == "/data"
    assert app["environment"]["HUAYU_TRUSTED_PROXY"] == "true"
    assert app["environment"]["HUAYU_BASE_URL"].startswith("https://")
    assert caddy["image"] == "caddy:2" and caddy["restart"] == "unless-stopped"
    assert {"80:80", "443:443"} <= set(caddy["ports"])
    assert any(v.startswith("./deploy/Caddyfile:/etc/caddy/Caddyfile") for v in caddy["volumes"])
    assert {"caddy-data:/data", "caddy-config:/config"} <= set(caddy["volumes"])
    assert set(cfg["volumes"]) == {"huayu-data", "caddy-data", "caddy-config"}


def test_compose_protects_the_data_volume_rotates_logs_and_caps_memory():
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
    cfg = yaml.safe_load((ROOT / "compose.yml").read_text())
    assert cfg["name"] == "huayu-coach", "a fixed project name: renaming the directory must not detach the volumes"
    data = cfg["volumes"]["huayu-data"]
    assert data == {"external": True, "name": "huayu-data"}, "`docker compose down -v` must not be able to remove the database"
    for service in ("app", "caddy"):
        log = cfg["services"][service]["logging"]
        assert log["driver"] == "json-file"
        assert log["options"]["max-size"] == "10m" and log["options"]["max-file"] == "5", "option values must be strings"
        mem = cfg["services"][service]["mem_limit"]
        assert re.fullmatch(r"\d+[mg]", mem), mem
    assert cfg["services"]["app"]["mem_limit"] == "768m"


def _caddy_lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.parametrize("name", ("Caddyfile", "Caddyfile.systemd"))
def test_caddyfiles_have_timeouts_a_body_cap_and_balanced_braces(name):
    lines = _caddy_lines(DEPLOY / name)
    text = "\n".join(lines)
    assert text.count("{") == text.count("}"), "unbalanced braces"
    assert lines[0] == "{", "the global options block must come first"
    global_block = text[: text.index("\n}\n") + 2]
    assert "servers {" in global_block and "timeouts {" in global_block
    for directive, value in (("read_header", "10s"), ("read_body", "30s"), ("idle", "2m")):
        assert re.search(rf"^{directive}\s+{value}$", global_block, flags=re.M), directive
    # the biggest legitimate upload is the 1 MB question import (app/routers/admin.py): 3 MB is room for multipart overhead
    assert re.search(r"request_body \{\s*max_size 3MB\s*\}", text)
    assert not re.search(r"^log\b", text, flags=re.M), "a Caddy access log would record /reset/<token> links"


def test_caddyfile_proxies_to_the_app_without_repeating_its_security_headers():
    lines = _caddy_lines(DEPLOY / "Caddyfile")
    assert "{$DOMAIN} {" in lines and "reverse_proxy app:8000" in lines and "encode zstd gzip" in lines
    body = "\n".join(lines).lower()
    for header_the_app_sets in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy",
                                "permissions-policy", "strict-transport-security"):
        assert header_the_app_sets not in body, f"{header_the_app_sets} is set by the app; repeating it would send it twice"
    assert "reverse_proxy 127.0.0.1:8000" in (DEPLOY / "Caddyfile.systemd").read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("script", ("backup.sh", "restore.sh"))
def test_shell_scripts_are_valid_and_executable(script):
    path = DEPLOY / script
    assert os.access(path, os.X_OK), f"{script} is not executable"
    assert path.read_text().startswith("#!/usr/bin/env bash") and "set -euo pipefail" in path.read_text()
    done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_backup_script_keeps_fourteen_and_restore_asks_first():
    backup, restore = (DEPLOY / "backup.sh").read_text(), (DEPLOY / "restore.sh").read_text()
    assert 'KEEP="${KEEP:-14}"' in backup and "docker compose exec -T app python -" in backup and "crontab" in backup
    assert "gzip -t" in backup and "gzip -t" in restore and "RESTORE" in restore and "docker compose stop app" in restore
    assert "trap " in backup and "trap " in restore, "both scripts must clean up / restart on any exit"
    assert "xargs" not in backup and "ls -1t" not in backup, "word-splitting retention breaks on a BACKUP_DIR with spaces"


@pytest.fixture
def fake_docker(tmp_path):
    """A `docker` and a `curl` on PATH that run the helpers with the local Python, so the shell logic can be exercised."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    docker = bin_dir / "docker"
    docker.write_text(f"""#!/usr/bin/env bash
echo "docker $1 $2" >> "{log}"
[ "$1" = compose ] || exit 99
shift; sub="$1"; shift
case "$sub" in
  exec) [ "${{FAKE_EXEC_FAIL:-0}}" = 1 ] && {{ echo "container not running" >&2; exit 1; }}
        [ "${{FAKE_EXEC_EMPTY:-0}}" = 1 ] && {{ cat > /dev/null; exit 0; }}
        exec "{sys.executable}" - ;;
  run)  while [ $# -gt 0 ] && [ "$1" != python ]; do shift; done; shift; exec "{sys.executable}" "$@" ;;
  stop) exit 0 ;;
  up)   [ "${{FAKE_UP_FAIL:-0}}" = 1 ] && exit 1; exit 0 ;;
  *)    exit 98 ;;
esac
""")
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\necho "curl $*" >> "{log}"\n')
    docker.chmod(0o755)
    curl.chmod(0o755)
    return bin_dir, log


def _huayu_db(path: Path, users: int = 3) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('0001')")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.executemany("INSERT INTO users (email) VALUES (?)", [(f"u{i}@example.com",) for i in range(users)])
    con.commit()
    con.close()


def _calls(log: Path) -> str:
    return log.read_text() if log.exists() else ""


def _run_script(script: str, args: list[str], env: dict, stdin: str = "", cwd=None):
    return subprocess.run(["bash", str(DEPLOY / script), *args], env=env, input=stdin, capture_output=True, text=True, cwd=cwd)


@pytest.mark.skipif(not HAS_BASH, reason="bash/gzip not available")
def test_backup_script_retention_survives_spaces_and_only_touches_its_own_files(tmp_path, fake_docker):
    import gzip
    bin_dir, log = fake_docker
    data = tmp_path / "data"
    data.mkdir()
    _huayu_db(data / "huayu.db")
    backups = tmp_path / "back ups"  # a space: the old `ls | xargs rm` retention silently deleted nothing here
    backups.mkdir()
    for n in range(1, 6):
        (backups / f"huayu-20200101-00000{n}.db.gz").write_bytes(b"old")
    (backups / "huayu-manual-copy.db.gz").write_bytes(b"mine")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HUAYU_DATA_DIR": str(data),
           "BACKUP_DIR": str(backups), "KEEP": "2", "BACKUP_PING_URL": "https://ping.example/abc/"}
    done = _run_script("backup.sh", [], env)
    assert done.returncode == 0, done.stderr
    names = sorted(p.name for p in backups.iterdir())
    assert len(names) == 3 and names[0] == "huayu-20200101-000005.db.gz" and "huayu-manual-copy.db.gz" in names
    newest = backups / names[1]  # the one just made sorts after the 2020 files and before "huayu-manual-copy"
    restored = tmp_path / "check.db"
    restored.write_bytes(gzip.decompress(newest.read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT count(*) FROM users").fetchone() == (3,)
    assert "curl -fsS -m 10 --retry 3 -o /dev/null https://ping.example/abc/" in _calls(log)
    assert list(backups.glob("*.partial")) == []


@pytest.mark.skipif(not HAS_BASH, reason="bash/gzip not available")
@pytest.mark.parametrize("keep", ("0", "abc", ""))
def test_backup_script_rejects_a_bad_keep(tmp_path, fake_docker, keep):
    bin_dir, _ = fake_docker
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "BACKUP_DIR": str(tmp_path / "b"), "KEEP": keep}
    done = _run_script("backup.sh", [], env)
    if keep == "":  # an empty KEEP falls back to the default of 14, which is fine
        return
    assert done.returncode == 2 and "KEEP" in done.stderr


@pytest.mark.skipif(not HAS_BASH, reason="bash/gzip not available")
@pytest.mark.parametrize("failure", ("FAKE_EXEC_FAIL", "FAKE_EXEC_EMPTY"))
def test_backup_script_failure_leaves_no_partial_file_and_pings_fail(tmp_path, fake_docker, failure):
    bin_dir, log = fake_docker
    backups = tmp_path / "b"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "BACKUP_DIR": str(backups), failure: "1",
           "BACKUP_PING_URL": "https://ping.example/abc"}
    done = _run_script("backup.sh", [], env)
    assert done.returncode != 0 and "BACKUP FAILED" in done.stderr
    assert list(backups.iterdir()) == [], "a failed or empty backup must not leave a file that looks like a backup"
    assert "https://ping.example/abc/fail" in _calls(log)


@pytest.mark.skipif(not HAS_BASH, reason="bash/gzip not available")
def test_restore_script_refuses_bad_files_before_stopping_the_app_and_restarts_it_after_a_late_refusal(tmp_path, fake_docker):
    import gzip
    bin_dir, log = fake_docker
    data = tmp_path / "data"
    data.mkdir()
    live = data / "huayu.db"
    _huayu_db(live, users=3)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HUAYU_DATA_DIR": str(data)}
    work = tmp_path / "in dir"
    work.mkdir()
    foreign = work / "foreign.db"
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE other (x)")
    con.commit()
    con.close()
    files = {"empty.gz": b"", "plain.gz": b"hello", "garbage.db.gz": gzip.compress(os.urandom(5000)),
             "emptyinside.db.gz": gzip.compress(b""), "foreign.db.gz": gzip.compress(foreign.read_bytes())}
    for name, content in files.items():
        (work / name).write_bytes(content)

    for name in ("missing.gz", "empty.gz", "plain.gz", "garbage.db.gz", "emptyinside.db.gz"):
        log.unlink(missing_ok=True)
        done = _run_script("restore.sh", [name], env, stdin="RESTORE\n", cwd=work)
        assert done.returncode != 0, name
        assert _calls(log) == "", f"{name}: the app must not be stopped for a file that is refused up front"

    log.unlink(missing_ok=True)  # valid gzip + SQLite header, but not a Huayu database: caught inside the container, after the stop
    done = _run_script("restore.sh", ["foreign.db.gz"], env, stdin="RESTORE\n", cwd=work)
    assert done.returncode != 0 and "not a Huayu Coach database" in done.stderr
    calls = _calls(log)
    assert "compose stop" in calls and "compose run" in calls and "compose up" in calls
    assert calls.index("compose stop") < calls.index("compose run") < calls.index("compose up"), "the app must be started again"
    assert sqlite3.connect(live).execute("SELECT count(*) FROM users").fetchone() == (3,), "the live database is untouched"
    assert not (data / "huayu.db.restoring").exists()


@pytest.mark.skipif(not HAS_BASH, reason="bash/gzip not available")
def test_restore_script_restores_a_relative_path_and_cancel_changes_nothing(tmp_path, fake_docker):
    import gzip
    bin_dir, log = fake_docker
    data = tmp_path / "data"
    data.mkdir()
    live = data / "huayu.db"
    _huayu_db(live, users=3)
    backup_src = tmp_path / "b.db"
    _huayu_db(backup_src, users=7)
    work = tmp_path / "backups dir"
    work.mkdir()
    (work / "good.db.gz").write_bytes(gzip.compress(backup_src.read_bytes()))
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HUAYU_DATA_DIR": str(data)}

    done = _run_script("restore.sh", ["good.db.gz"], env, stdin="nope\n", cwd=work)  # a relative name given from the backups folder
    assert done.returncode == 1 and "Cancelled" in done.stdout and _calls(log) == ""

    done = _run_script("restore.sh", ["good.db.gz"], env, stdin="RESTORE\n", cwd=work)
    assert done.returncode == 0, done.stderr
    assert sqlite3.connect(live).execute("SELECT count(*) FROM users").fetchone() == (7,)
    assert len(list(data.glob("huayu.db.before-restore-*"))) == 1
    assert "deleted after this backup" in done.stdout, "the deleted-accounts reminder must be printed"
    assert "compose stop" in _calls(log) and "compose up" in _calls(log)


def test_systemd_unit_is_hardened_and_restarts():
    unit = (DEPLOY / "huayu-coach.service").read_text()
    for needed in ("Restart=always", "EnvironmentFile=/etc/huayu-coach.env", "User=huayu", "NoNewPrivileges=true",
                   "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "HUAYU_ENV=prod", "app.cli prepare",
                   "--proxy-headers", "[Install]", "StateDirectory=huayu-coach", "--no-access-log", "MemoryMax="):
        assert needed in unit, needed
    # systemd lets EnvironmentFile override Environment=, so the forced production values go through `env` on the command lines
    assert not re.search(r"^Environment=.*HUAYU_(ENV|DATA_DIR|TRUSTED_PROXY)", unit, flags=re.M)
    for line in unit.splitlines():
        if line.startswith(("ExecStart=", "ExecStartPre=")):
            assert line.split("=", 1)[1].startswith("/usr/bin/env HUAYU_ENV=prod HUAYU_DATA_DIR=/var/lib/huayu-coach "), line
    # nothing in the unit may have a trailing comment on a setting line (systemd does not support them)
    assert not [ln for ln in unit.splitlines() if "=" in ln and not ln.startswith("#") and re.search(r"\s#\s", ln)]


def test_env_example_is_valid_for_docker_compose_systemd_and_dev():
    """No inline comments and no `export`: the same lines must be read the same way by Compose, systemd and python-dotenv."""
    names = set()
    for number, line in enumerate((ROOT / ".env.example").read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\s#]*", line) or re.fullmatch(r"[A-Z][A-Z0-9_]*=('[^']*'|\"[^\"]*\")", line), \
            f".env.example line {number} is not a plain NAME=value: {line!r}"
        names.add(line.split("=", 1)[0])
    documented = (ROOT / ".env.example").read_text()
    missing = [f"HUAYU_{name.upper()}" for name in Settings.model_fields if f"HUAYU_{name.upper()}" not in documented]
    assert not missing, f"add these variables to .env.example (a commented example is enough): {missing}"
    assert "HUAYU_SMTP_SECURITY" in documented


def test_backup_and_restore_helpers_round_trip_a_live_wal_database(tmp_path):
    """The two Python helpers the scripts run inside the container, exercised against a real SQLite file."""
    env = {**os.environ, "HUAYU_DATA_DIR": str(tmp_path)}
    live = tmp_path / "huayu.db"
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('0001')")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    con.commit()
    con.execute("INSERT INTO t VALUES (999)")  # sits in the WAL while the app is "running": a plain copy would miss it
    con.commit()

    backup = subprocess.run([sys.executable, str(DEPLOY / "backup_db.py")], env=env, capture_output=True, check=True).stdout
    copy = tmp_path / "copy.db"
    copy.write_bytes(backup)
    assert sqlite3.connect(copy).execute("SELECT count(*) FROM t").fetchone() == (51,)

    con.execute("DELETE FROM t")
    con.commit()
    con.close()
    code = (DEPLOY / "restore_db.py").read_text()
    done = subprocess.run([sys.executable, "-c", code], env=env, input=backup, capture_output=True)
    assert done.returncode == 0, done.stderr
    assert sqlite3.connect(live).execute("SELECT count(*) FROM t").fetchone() == (51,)
    aside = list(tmp_path.glob("huayu.db.before-restore-*"))
    assert len(aside) == 1, "the replaced database is kept aside"
    assert oct(aside[0].stat().st_mode & 0o777) == "0o600", "the aside copy holds personal data: owner only"
    assert not (tmp_path / "huayu.db.restoring").exists()


def test_restore_helper_refuses_every_kind_of_wrong_file_and_changes_nothing(tmp_path):
    env = {**os.environ, "HUAYU_DATA_DIR": str(tmp_path)}
    live = tmp_path / "huayu.db"
    _huayu_db(live, users=3)
    code = (DEPLOY / "restore_db.py").read_text()
    good = tmp_path / "good.db"
    _huayu_db(good, users=9)
    full = good.read_bytes()

    foreign = tmp_path / "foreign.db"
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE other (x)")
    con.commit()
    con.close()
    no_revision = tmp_path / "norev.db"
    con = sqlite3.connect(no_revision)
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    tail_corrupt = bytearray(full)
    tail_corrupt[-120:] = os.urandom(120)
    cases = {"the backup is empty": b"", "not an SQLite database": b"not a database" * 300,
             "not a Huayu Coach database": foreign.read_bytes(), "no migration revision": no_revision.read_bytes(),
             "integrity_check": bytes(tail_corrupt), "integrity_check (truncated)": full[: len(full) // 2]}
    for expect, payload in cases.items():
        done = subprocess.run([sys.executable, "-c", code], env=env, input=payload, capture_output=True)
        assert done.returncode != 0, expect
        assert expect.split(" (")[0].encode() in done.stderr, (expect, done.stderr)
        assert b"nothing was changed" in done.stderr
        assert sqlite3.connect(live).execute("SELECT count(*) FROM users").fetchone() == (3,), "a bad backup must change nothing"
        assert not list(tmp_path.glob("huayu.db.before-restore-*")), "no aside copy for a refused file"
        assert not (tmp_path / "huayu.db.restoring").exists() and not (tmp_path / "huayu.db.restoring-wal").exists()


def test_backup_helper_is_consistent_while_a_writer_is_busy(tmp_path):
    """Pairs of rows are inserted in one transaction each: a consistent snapshot has equal counts in both tables."""
    import threading
    import time

    live = tmp_path / "huayu.db"
    _huayu_db(live)
    con = sqlite3.connect(live)
    con.execute("CREATE TABLE a (n INTEGER)")
    con.execute("CREATE TABLE b (n INTEGER)")
    con.commit()
    con.close()
    stop = threading.Event()

    def writer():
        w = sqlite3.connect(live, timeout=30, isolation_level=None)
        w.execute("PRAGMA busy_timeout=15000")
        n = 0
        while not stop.is_set():
            w.execute("BEGIN IMMEDIATE")
            w.execute("INSERT INTO a VALUES (?)", (n,))
            w.execute("INSERT INTO b VALUES (?)", (n,))
            w.execute("COMMIT")
            n += 1
        w.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        time.sleep(0.3)
        env = {**os.environ, "HUAYU_DATA_DIR": str(tmp_path)}
        for i in range(3):
            out = subprocess.run([sys.executable, str(DEPLOY / "backup_db.py")], env=env, capture_output=True, check=True).stdout
            snap = tmp_path / f"snap{i}.db"
            snap.write_bytes(out)
            c = sqlite3.connect(snap)
            assert c.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            na, nb = c.execute("SELECT count(*) FROM a").fetchone()[0], c.execute("SELECT count(*) FROM b").fetchone()[0]
            c.close()
            assert na == nb and na > 0
    finally:
        stop.set()
        thread.join()


def test_backup_helper_refuses_a_missing_database(tmp_path):
    done = subprocess.run([sys.executable, str(DEPLOY / "backup_db.py")], env={**os.environ, "HUAYU_DATA_DIR": str(tmp_path)},
                          capture_output=True)
    assert done.returncode != 0 and b"no database" in done.stderr and not done.stdout


def test_deploy_docs_use_commands_that_really_exist():
    cli = subprocess.run([sys.executable, "-m", "app.cli", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert cli.returncode == 0 and "prepare" in cli.stdout and "create-admin" in cli.stdout
    guide = (DEPLOY / "deploy.md").read_text()
    for needed in ("docker compose up -d --build", "python -m app.cli create-admin", "/healthz", "deploy/backup.sh",
                   "deploy/restore.sh", "ufw", "HUAYU_SECRET_KEY", "CHƯA KIỂM THỬ"):
        assert needed in guide, needed


def test_deploy_guide_has_the_safety_content_the_review_asked_for():
    guide = (DEPLOY / "deploy.md").read_text()
    for needed in (
        "ssh-copy-id", "PreferredAuthentications=publickey", "sshd -t", "ufw allow OpenSSH",   # SSH: key proven before passwords go off
        "docker tag huayu-coach:latest huayu-coach:before-", "--no-build",                      # a real rollback
        "docker volume create huayu-data", "down -v",                                          # volume protection
        "BACKUP_PING_URL", "MAILTO", "KEEP",                                                    # alerting and retention
        "huayu.db.before-restore-", "outbox", "rsync -av deploy@", "-mtime +14",                # what survives, offsite lifecycle
        "AAAA", "HUAYU_SMTP_SECURITY", "swap", "mem_limit",                                     # IPv6, SMTP, sizing
    ):
        assert needed in guide, needed


def test_paths_named_in_the_deploy_guide_and_readme_exist():
    """Every repo path mentioned (deploy/..., app/..., tests/..., migrations/...) must exist, so the docs cannot rot."""
    token = re.compile(r"(?<![\w/.-])(?:\./)?((?:deploy|app|tests|migrations|seeds|scripts)/[\w./-]*[\w/])")
    missing = []
    for doc in (DEPLOY / "deploy.md", ROOT / "README.md"):
        for match in token.finditer(doc.read_text(encoding="utf-8")):
            path = match.group(1)
            if (ROOT / path).exists():
                continue
            missing.append(f"{doc.name}: {path}")
    assert not missing, sorted(set(missing))


def test_every_huayu_variable_in_the_deploy_files_is_a_real_setting():
    real = {f"HUAYU_{name.upper()}" for name in Settings.model_fields} | {"HUAYU_ADMIN_PASSWORD", "HUAYU_ROUTERS", "HUAYU_USER"}
    for path in (DEPLOY / "deploy.md", ROOT / ".env.example", ROOT / "compose.yml", DEPLOY / "huayu-coach.service",
                 DEPLOY / "backup.sh"):
        for name in set(re.findall(r"HUAYU_[A-Z_]+", path.read_text(encoding="utf-8"))):
            assert name in real, f"{path.name} mentions {name}, which is not a setting"


def test_icon_generator_reproduces_the_committed_icon():
    spec = importlib.util.spec_from_file_location("make_icons", DEPLOY / "make_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.png(192, True) == (STATIC / "manifest" / "icon-192.png").read_bytes()


# ----------------------------------------------------------------------------- README
def test_readme_is_bilingual_and_documents_every_setting():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Tiếng Việt" in readme and "English" in readme
    assert readme.index("## Tiếng Việt") < readme.index("## English")
    undocumented = [f"HUAYU_{name.upper()}" for name in Settings.model_fields if f"HUAYU_{name.upper()}" not in readme]
    assert not undocumented, f"add these variables to the README table: {undocumented}"
    for needed in ("./start.sh", "python -m app.cli", "pytest", "deploy/deploy.md"):
        assert needed in readme, needed
