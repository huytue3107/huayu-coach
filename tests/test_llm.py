from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services import llm

ITEMS = [{"action": "reduce_new_grammar", "params": {"value": 2}, "evidence": [{"type": "plan_value", "value": 5}]},
         {"action": "change_exercise_count", "params": {"value": 5}, "evidence": []}]
PLAN = {"daily_minutes": 30, "new_grammar": 5, "exercise_count": 8}


def settings(provider="openai", **kw):
    return Settings(env="test", llm_provider=provider, llm_model="m", llm_api_key="k", **kw)


def client_returning(text, status=200, anthropic=False, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        body = {"content": [{"type": "text", "text": text}]} if anthropic else {"choices": [{"message": {"content": text}}]}
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_disabled_by_default_makes_no_call():
    assert llm.explain("too_hard", ITEMS, PLAN, "vi", Settings(env="test")) is None


def test_good_reply_is_returned_and_only_structured_data_is_sent():
    seen = []
    out = llm.explain("too_hard", ITEMS, PLAN, "en", settings(),
                      client_returning("Let's lighten new grammar from 5 to 2 and exercises from 8 to 5. Nothing changes until you confirm.", seen=seen))
    assert out and "5 to 2" in out
    sent = json.dumps(seen[0], ensure_ascii=False)
    assert "too_hard" in sent and "reduce_new_grammar" in sent
    assert "self_report" not in sent and "@" not in sent  # no free text, no e-mail


def test_a_reply_with_an_invented_number_is_rejected():
    out = llm.explain("too_hard", ITEMS, PLAN, "en", settings(), client_returning("Cut new grammar to 1 and study 45 minutes."))
    assert out is None


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_degrade_to_none(status):
    assert llm.explain("too_hard", ITEMS, PLAN, "vi", settings(), client_returning("x", status=status)) is None


def test_timeout_degrades_to_none():
    def handler(request):
        raise httpx.ReadTimeout("slow")
    assert llm.explain("too_hard", ITEMS, PLAN, "vi", settings(), httpx.Client(transport=httpx.MockTransport(handler))) is None


def test_anthropic_shape_and_empty_reply():
    ok = llm.explain("need_rest", [], PLAN, "zh-TW", settings("anthropic"),
                     client_returning("先休息一天，沒有任何改變直到你確認。", anthropic=True))
    assert ok and "休息" in ok
    assert llm.explain("need_rest", [], PLAN, "zh-TW", settings("anthropic"), client_returning("", anthropic=True)) is None


def test_provider_without_key_or_model_is_a_config_error():
    with pytest.raises(ValueError):
        Settings(env="test", llm_provider="openai")
