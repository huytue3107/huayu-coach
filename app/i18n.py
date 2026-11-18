"""Tiny i18n: app/locales/<lang>/<namespace>.json, keys are addressed as "<namespace>.<path>".

Lookup order: requested language -> English -> the key itself (so a missing string is visible, not blank).
Placeholders use {name}. Translations never contain HTML.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.requests import Request

from .config import LOCALES, get_settings
from .security import LANG_COOKIE

LOCALE_DIR = Path(__file__).parent / "locales"
LANGUAGE_NAMES = {"vi": "Tiếng Việt", "zh-TW": "繁體中文", "en": "English"}


def _flatten(prefix: str, data: dict[str, Any], out: dict[str, str]) -> None:
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            _flatten(key, v, out)
        else:
            out[key] = str(v)


@lru_cache
def catalog(locale: str) -> dict[str, str]:
    out: dict[str, str] = {}
    folder = LOCALE_DIR / locale
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            _flatten(path.stem, json.loads(path.read_text(encoding="utf-8")), out)
    return out


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def t(locale: str, key: str, **params: Any) -> str:
    text = catalog(locale).get(key) or catalog("en").get(key) or key
    return text.format_map(_Safe(params)) if params else text


def reload_catalogs() -> None:
    catalog.cache_clear()


def normalize(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip()
    for loc in LOCALES:
        if c.lower() == loc.lower():
            return loc
    base = c.split("-")[0].lower()
    if base == "zh":
        return "zh-TW"
    return base if base in LOCALES else None


def pick_locale(request: Request, user_locale: str | None = None) -> str:
    """Logged-in preference > language cookie > Accept-Language > default."""
    if (loc := normalize(user_locale)):
        return loc
    if (loc := normalize(request.cookies.get(LANG_COOKIE))):
        return loc
    for part in request.headers.get("accept-language", "").split(","):
        if (loc := normalize(part.split(";")[0])):
            return loc
    return get_settings().default_locale
