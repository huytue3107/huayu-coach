"""Optional AI wording for a proposal. The rule engine already decided every number; the model only explains.

Three safeguards, all enforced in code (not just in the prompt):
  1. only the reason CODE and structured facts are sent - never the learner's free text, name or e-mail;
  2. the reply is rejected if it contains any number that is not in the data we sent (the model cannot invent a plan);
  3. every failure (no key, timeout, bad reply, guard violation) returns None and the UI falls back to template text.
The caller must also have the learner's consent to send data to an AI provider (Consent.llm_transfer_agreed).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("huayu.llm")
_slots = threading.BoundedSemaphore(4)  # a slow provider must not tie up every worker thread

LANG_NAME = {"vi": "Vietnamese", "zh-TW": "Traditional Chinese as used in Taiwan", "en": "English"}

SYSTEM = (
    "You are a warm, patient Mandarin learning coach for beginner (A1-A2) learners. "
    "A rule engine has already produced adjustments to the learner's study plan. Your only job is to explain them kindly. "
    "Rules: use ONLY the facts and numbers in the JSON you are given; never add, change or suggest any other adjustment or number; "
    "never claim anything has already been changed - everything is a proposal until the learner confirms; "
    "say that the learner may accept some, all, or none; do not blame the learner; no lists, no markdown, at most 90 words. "
    "Write in {lang}. Keep Chinese words as they are."
)


def _numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", text)}


def _client_call(settings: Settings, system: str, user: str, client: httpx.Client) -> str | None:
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    timeout = settings.llm_timeout_s
    if settings.llm_provider == "anthropic":
        r = client.post(settings.llm_base_url or "https://api.anthropic.com/v1/messages", timeout=timeout,
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                        json={"model": settings.llm_model, "max_tokens": 400, "system": system,
                              "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip() or None
    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    cap = "max_completion_tokens" if base == "https://api.openai.com/v1" else "max_tokens"
    r = client.post(f"{base}/chat/completions", timeout=timeout,
                    headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                    json={"model": settings.llm_model, cap: 1200,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip() or None


def explain(reason: str, items: list[dict[str, Any]], plan: dict[str, Any], locale: str,
            settings: Settings | None = None, client: httpx.Client | None = None) -> str | None:
    """items = [{"action": code, "params": {...}, "evidence": [...]}]. Returns friendly text or None."""
    settings = settings or get_settings()
    if settings.llm_provider == "none":
        return None
    payload = json.dumps({"reason": reason, "current_plan": plan, "proposed_adjustments": items}, ensure_ascii=False)
    allowed = _numbers(payload) | {1, 2, 3}
    system = SYSTEM.format(lang=LANG_NAME.get(locale, "English"))
    if not _slots.acquire(blocking=False):
        log.warning("llm busy: using template wording")
        return None
    own = client is None
    client = client or httpx.Client()
    try:
        text = _client_call(settings, system, payload, client)
    except Exception as exc:  # noqa: BLE001 - any provider failure must degrade to template text
        log.warning("llm explanation failed: %s", type(exc).__name__)
        return None
    finally:
        _slots.release()
        if own:
            client.close()
    if not text or len(text) > 900:
        return None
    stray = _numbers(text) - allowed
    if stray:
        log.warning("llm reply rejected: numbers not in the data: %s", sorted(stray))
        return None
    return text
