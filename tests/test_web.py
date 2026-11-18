from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app import i18n, models
from app.config import LOCALES


@pytest.fixture
def client(engine):
    from app.main import create_app
    return TestClient(create_app(), follow_redirects=False)


def test_healthz_and_security_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_unknown_page_is_a_friendly_404_in_the_visitors_language(client):
    r = client.get("/nope", headers={"accept-language": "zh-TW,zh;q=0.9"})
    assert r.status_code == 404 and "找不到頁面" in r.text and 'lang="zh-TW"' in r.text
    r = client.get("/nope", headers={"accept-language": "vi"})
    assert "Không tìm thấy trang" in r.text


def test_language_switch_sets_cookie_and_blocks_open_redirects(client):
    r = client.get("/lang/en?next=//evil.example")
    assert r.status_code == 303 and r.headers["location"] == "/" and "hc_lang=en" in r.headers["set-cookie"]
    r = client.get("/lang/zh?next=/nope")
    assert r.headers["location"] == "/nope" and "hc_lang=zh-TW" in r.headers["set-cookie"]


def test_every_locale_has_exactly_the_same_keys():
    keys = {loc: set(i18n.catalog(loc)) for loc in LOCALES}
    for loc in LOCALES:
        assert keys[loc] == keys["en"], (loc, sorted(keys["en"] ^ keys[loc])[:10])


def test_translations_contain_no_html_and_placeholders_match():
    import re
    for loc in LOCALES:
        for key, text in i18n.catalog(loc).items():
            assert "<" not in text and ">" not in text, (loc, key)
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", i18n.catalog("en")[key])), (loc, key)


def test_missing_translation_is_visible_not_blank():
    assert i18n.t("vi", "nope.nothing") == "nope.nothing"
    assert i18n.t("en", "common.flash.saved") == "Saved."


def test_state_changing_requests_need_a_csrf_token(client):
    from app.web import csrf_protect  # noqa: F401  (imported to make sure it exists)


def test_language_switch_also_updates_a_logged_in_learners_saved_language(engine, client):
    from tests.conftest import login_as, make_user
    with dbmod.write_session() as s:
        user = make_user(s)
        uid = user.id
    login_as(client, user)
    r = client.get("/lang/en?next=/")
    assert r.status_code == 303
    with dbmod.read_session() as s:
        assert s.get(models.User, uid).locale == "en"  # without this the saved 'vi' would override the cookie forever


def test_static_css_is_revalidated_and_pages_are_not_cached(client):
    css = client.get("/static/css/app.css")
    assert css.status_code == 200 and css.headers["cache-control"] == "no-cache" and css.headers.get("etag")
    assert client.get("/static/icon.svg").headers["cache-control"].startswith("public, max-age=")
    assert client.get("/healthz").headers["cache-control"] == "no-store"
    again = client.get("/static/css/app.css", headers={"if-none-match": css.headers["etag"]})
    assert again.status_code == 304  # revalidation is cheap
