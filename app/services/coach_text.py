"""Words for the coaching screens: structured data in, a natural sentence in the learner's language out.

Everything here is pure (no database, no request). The rule engine stores facts as data
(`SuggestionItem.params`, `SuggestionItem.evidence`, `PlanChange.old_values/new_values`); this module is the only
place that turns them into prose, through the i18n keys

    coach.item.<action_code>      coach.item_title.<action_code>
    coach.evidence.<type>         coach.field.<plan field>

Honesty rules that live in the wording, not only in the UI: a percentage is never printed (accuracy is quoted as
"x of y"), `few_data` says plainly that there are too few answers, and nothing claims that a change already happened.
Values come from JSON columns, so every parameter is coerced defensively: a malformed row renders "?" instead of
crashing the page. Output is plain text; templates escape it (never mark it safe).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .. import i18n
from ..domain import actions

# action code -> (plan field that holds the "old" value, name of the param holding the new value)
_NUMERIC_ACTIONS = {
    "reduce_new_grammar": ("new_grammar", "value"),
    "change_exercise_count": ("exercise_count", "value"),
    "change_daily_minutes": ("daily_minutes", "value"),
    "split_session": ("sessions_per_day", "sessions"),
}
FIELD_ORDER = ("daily_minutes", "new_grammar", "exercise_count", "sessions_per_day", "focus_minutes", "review_topics",
               "rest_until")
LABELLED_FIELDS = ("daily_minutes", "new_grammar", "exercise_count", "sessions_per_day", "focus_minutes")
SKILLS = ("grammar", "vocab", "reading")
EVIDENCE_TYPES = ("self_report", "plan_value", "accuracy", "few_data", "weak_topic", "completion", "available_minutes",
                  "plan_at_minimum")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# ----------------------------------------------------------------------------- coercion helpers
def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value.strip()):
        return int(value)
    return None


def _num(value: Any) -> str:
    n = _int(value)
    return "?" if n is None else str(n)


def _text(value: Any, limit: int = 40) -> str:
    if value is None:
        return "?"
    return _CONTROL.sub("", str(value)).strip()[:limit] or "?"


def _get(plan: Any, name: str) -> Any:
    if plan is None:
        return None
    return plan.get(name) if isinstance(plan, Mapping) else getattr(plan, name, None)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _topic_names(value: Any) -> list[str]:
    """review_topics comes as [{"topic","until"}] (plan row) or [[topic, until], ...] (audit row)."""
    names: list[str] = []
    for entry in value if isinstance(value, (list, tuple)) else []:
        if isinstance(entry, Mapping):
            raw = entry.get("topic")
        elif isinstance(entry, (list, tuple)) and entry:
            raw = entry[0]
        else:
            raw = entry
        if isinstance(raw, str) and raw.strip():
            names.append(_text(raw, 30))
    return names


# ----------------------------------------------------------------------------- dates
def format_date(locale: str, value: Any) -> str:
    """A date in the reader's habit: 19/11/2026 (vi), 2026年11月19日 (zh-TW), 2026-11-19 (en)."""
    d = _as_date(value)
    if d is None:
        return _text(value)
    if locale == "zh-TW":
        return i18n.t(locale, "coach.date.fmt", y=d.year, m=d.month, d=d.day)
    return i18n.t(locale, "coach.date.fmt", y=f"{d.year:04d}", m=f"{d.month:02d}", d=f"{d.day:02d}")


# ----------------------------------------------------------------------------- reasons
def reason_title(locale: str, reason: str) -> str:
    if reason in actions.REASONS:
        return i18n.t(locale, f"coach.reason.{reason}.title")
    return _text(reason)


def summarize(locale: str, reason: str) -> str:
    """The calm template explanation shown when there is no AI wording. It is about the reason, never about numbers."""
    key = reason if reason in actions.REASONS else "default"
    return i18n.t(locale, f"coach.summary.{key}")


def glance(locale: str, action_codes: list[str]) -> str:
    """One line built from the items themselves ("In short: Fewer new grammar points, Fewer exercises."), shown under the
    template summary. Empty when there are no items."""
    titles = [describe_title(locale, code) for code in action_codes]
    return i18n.t(locale, "coach.proposal.glance", titles=i18n.t(locale, "coach.value.sep").join(titles)) if titles else ""


# ----------------------------------------------------------------------------- suggestion items
def describe_title(locale: str, action_code: str) -> str:
    key = action_code if action_code in actions.ACTION_CODES else "unknown"
    return i18n.t(locale, f"coach.item_title.{key}")


def describe_item(locale: str, action_code: str, params: Mapping[str, Any] | None, plan: Any = None) -> str:
    """One suggestion as a sentence. `plan` (a snapshot dict or a StudyPlan) supplies the "old" number;
    without it the sentence states only the new value."""
    params = params if isinstance(params, Mapping) else {}
    if action_code in _NUMERIC_ACTIONS:
        old_field, param = _NUMERIC_ACTIONS[action_code]
        new = _num(params.get(param))
        old = _int(_get(plan, old_field))
        suffix = "" if old is not None else "_plain"
        return i18n.t(locale, f"coach.item.{action_code}{suffix}", old="?" if old is None else old, new=new, sessions=new)
    if action_code == "pomodoro":
        return i18n.t(locale, "coach.item.pomodoro", minutes=_num(params.get("focus_minutes")))
    if action_code == "take_rest":
        days = _int(params.get("days"))
        key = "coach.item.take_rest_one" if days == 1 else "coach.item.take_rest"
        return i18n.t(locale, key, days=_num(days))
    if action_code == "review_topic":
        return i18n.t(locale, "coach.item.review_topic", topic=_text(params.get("topic"), 30))
    return i18n.t(locale, "coach.item.unknown")


# ----------------------------------------------------------------------------- evidence
def describe_evidence(locale: str, fact: Mapping[str, Any]) -> str:
    """One piece of evidence as a sentence. Returns "" for an unknown type so callers can simply skip it."""
    if not isinstance(fact, Mapping):
        return ""
    kind = fact.get("type")
    t = i18n.t
    if kind == "self_report":
        return t(locale, "coach.evidence.self_report", reason=reason_title(locale, str(fact.get("reason"))))
    if kind == "plan_value":
        name = str(fact.get("field"))
        label = t(locale, f"coach.label.{name}") if name in LABELLED_FIELDS else _text(name)
        return t(locale, "coach.evidence.plan_value", field=label, value=_num(fact.get("value")))
    if kind in ("accuracy", "few_data", "weak_topic", "completion", "available_minutes", "plan_at_minimum"):
        return _describe_data_evidence(locale, kind, fact)
    return ""


def _describe_data_evidence(locale: str, kind: str, fact: Mapping[str, Any]) -> str:
    t = i18n.t
    skill_code = str(fact.get("skill"))
    skill = t(locale, f"coach.skill.{skill_code}") if skill_code in SKILLS else _text(skill_code)
    if kind == "accuracy":
        return t(locale, "coach.evidence.accuracy", skill=skill, correct=_num(fact.get("correct")),
                 total=_num(fact.get("total")), days=_num(fact.get("days")))
    if kind == "few_data":
        total = _int(fact.get("total"))
        key = "few_data_zero" if total == 0 else "few_data_one" if total == 1 else "few_data"
        return t(locale, f"coach.evidence.{key}", skill=skill, total=_num(total))
    if kind == "weak_topic":
        return t(locale, "coach.evidence.weak_topic", topic=_text(fact.get("topic"), 30), wrong=_num(fact.get("wrong")),
                 days=_num(fact.get("days")))
    if kind == "completion":
        return t(locale, "coach.evidence.completion", done=_num(fact.get("done")), scheduled=_num(fact.get("scheduled")),
                 days=_num(fact.get("days")))
    if kind == "available_minutes":
        return t(locale, "coach.evidence.available_minutes", value=_num(fact.get("value")))
    return t(locale, "coach.evidence.plan_at_minimum")


# ----------------------------------------------------------------------------- plan changes (audit rows)
def _value(locale: str, field: str, value: Any) -> str:
    if field == "review_topics":
        names = _topic_names(value)
        return i18n.t(locale, "coach.value.sep").join(names) if names else i18n.t(locale, "coach.value.none")
    if field == "rest_until":
        return format_date(locale, value)
    if value is None:
        return i18n.t(locale, "coach.value.none")
    n = _int(value)
    return _text(value) if n is None else str(n)


def describe_change(locale: str, field: str, old: Any, new: Any) -> str:
    """"Daily study time: 30 → 15 minutes" for one plan field."""
    key = field if field in FIELD_ORDER else "unknown"
    return i18n.t(locale, f"coach.field.{key}", old=_value(locale, field, old), new=_value(locale, field, new))


def describe_plan_change(locale: str, old_values: Any, new_values: Any) -> list[dict[str, str]]:
    """Every field that differs between two PlanChange value dicts, in a stable order, as [{"field", "text"}].
    Fields that are equal on both sides (a manual edit stores the whole plan) are left out."""
    old_values = old_values if isinstance(old_values, Mapping) else {}
    new_values = new_values if isinstance(new_values, Mapping) else {}
    lines: list[dict[str, str]] = []
    for field in FIELD_ORDER:
        if field not in new_values:
            continue
        old, new = old_values.get(field), new_values[field]
        same = _topic_names(old) == _topic_names(new) if field == "review_topics" else old == new
        if not same:
            lines.append({"field": field, "text": describe_change(locale, field, old, new)})
    return lines
