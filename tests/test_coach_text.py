"""coach_text turns structured coach data into sentences. Every action code, evidence type and plan field the rule engine
can produce must render in all three languages with no raw key and no unfilled {placeholder}."""
from __future__ import annotations

import itertools
import json
import re
from datetime import date

import pytest

from app import i18n
from app.config import LOCALES
from app.domain import actions
from app.domain.actions import PlanState
from app.domain.diagnosis import LearnerStats, build_items
from app.services import coach_text as ct

PLAN = {"daily_minutes": 30, "new_grammar": 5, "exercise_count": 8, "sessions_per_day": 1, "focus_minutes": None,
        "review_topics": []}
PARAMS = {
    "reduce_new_grammar": {"value": 2}, "change_exercise_count": {"value": 5}, "change_daily_minutes": {"value": 15},
    "split_session": {"sessions": 2}, "pomodoro": {"focus_minutes": 10}, "take_rest": {"days": 3},
    "review_topic": {"topic": "了"},
}
EVIDENCE = [
    {"type": "self_report", "reason": "too_hard"}, {"type": "plan_value", "field": "new_grammar", "value": 5},
    {"type": "accuracy", "skill": "grammar", "correct": 9, "total": 20, "days": 14},
    {"type": "few_data", "skill": "grammar", "total": 3}, {"type": "few_data", "skill": "grammar", "total": 1},
    {"type": "few_data", "skill": "vocab", "total": 0},
    {"type": "weak_topic", "topic": "了", "wrong": 7, "days": 14},
    {"type": "completion", "done": 1, "scheduled": 3, "days": 3},
    {"type": "available_minutes", "value": 20}, {"type": "plan_at_minimum"},
]
SIMPLIFIED_ONLY = set("这们个时习计划务练题请说话间设还进选继续让给应该觉错对没问关机会样让钟读动结认为")


def clean(text: str) -> None:
    assert text.strip(), "empty sentence"
    assert not re.search(r"\bcoach\.\w+", text), f"raw key in: {text}"
    assert "{" not in text and "}" not in text, f"unfilled placeholder in: {text}"
    assert "<" not in text and ">" not in text
    assert "None" not in text and "?" not in text, f"missing value in: {text}"


# ----------------------------------------------------------------------------- items
def test_the_sample_set_covers_every_action_code():
    assert set(PARAMS) == set(actions.ACTION_CODES)


@pytest.mark.parametrize("locale,code", list(itertools.product(LOCALES, actions.ACTION_CODES)))
def test_every_action_code_has_a_title_and_a_sentence_in_every_locale(locale, code):
    title = ct.describe_title(locale, code)
    clean(title)
    for plan in (PLAN, None):
        text = ct.describe_item(locale, code, PARAMS[code], plan)
        clean(text)
        value = str(next(iter(PARAMS[code].values())))
        assert value in text


@pytest.mark.parametrize("locale", LOCALES)
def test_numeric_items_show_old_and_new_when_the_plan_is_known(locale):
    for code, old, new in (("reduce_new_grammar", "5", "2"), ("change_exercise_count", "8", "5"),
                           ("change_daily_minutes", "30", "15")):
        with_plan = ct.describe_item(locale, code, PARAMS[code], PLAN)
        assert old in with_plan and new in with_plan and "→" in with_plan
        without = ct.describe_item(locale, code, PARAMS[code], None)
        assert new in without and "→" not in without
    split = ct.describe_item(locale, "split_session", {"sessions": 2}, PLAN)  # "... 2 shorter sessions (now: 1)"
    assert "2" in split and "1" in split
    assert "1" not in ct.describe_item(locale, "split_session", {"sessions": 2}, None)


@pytest.mark.parametrize("locale", LOCALES)
def test_a_plan_object_works_as_well_as_a_snapshot_dict(locale):
    class Row:
        new_grammar = 5

    assert ct.describe_item(locale, "reduce_new_grammar", {"value": 2}, Row()) == \
        ct.describe_item(locale, "reduce_new_grammar", {"value": 2}, {"new_grammar": 5})


@pytest.mark.parametrize("locale", LOCALES)
def test_rest_for_one_day_and_for_several_read_differently(locale):
    one, many = ct.describe_item(locale, "take_rest", {"days": 1}), ct.describe_item(locale, "take_rest", {"days": 3})
    clean(one), clean(many)
    assert one != many and "3" in many


@pytest.mark.parametrize("locale", LOCALES)
def test_unknown_or_broken_rows_never_crash_the_page(locale):
    assert ct.describe_item(locale, "launch_rocket", {}, PLAN) == i18n.t(locale, "coach.item.unknown")
    assert ct.describe_title(locale, "launch_rocket") == i18n.t(locale, "coach.item_title.unknown")
    for params in (None, [], "x", {"value": "abc"}, {"value": True}, {"value": None}):
        text = ct.describe_item(locale, "reduce_new_grammar", params, PLAN)  # type: ignore[arg-type]
        assert "{" not in text and "coach." not in text
    assert ct.describe_evidence(locale, {"type": "mystery"}) == ""
    assert ct.describe_evidence(locale, None) == ""  # type: ignore[arg-type]
    assert "coach." not in ct.describe_evidence(locale, {"type": "accuracy"})  # missing params -> "?", not a crash
    assert "\n" not in ct.describe_item(locale, "review_topic", {"topic": "a\nb\x00" + "x" * 99})


# ----------------------------------------------------------------------------- evidence
def test_the_sample_set_covers_every_evidence_type():
    assert {f["type"] for f in EVIDENCE} == set(ct.EVIDENCE_TYPES)


@pytest.mark.parametrize("locale,fact", list(itertools.product(LOCALES, EVIDENCE)), ids=lambda v: v if isinstance(v, str) else json.dumps(v))
def test_every_evidence_type_renders_in_every_locale(locale, fact):
    clean(ct.describe_evidence(locale, fact))


@pytest.mark.parametrize("locale", LOCALES)
def test_few_data_is_honest_and_never_prints_a_percentage(locale):
    for total in (0, 1, 3, 4):
        text = ct.describe_evidence(locale, {"type": "few_data", "skill": "grammar", "total": total})
        assert "%" not in text and "％" not in text
        if total > 0:
            assert str(total) in text
    assert "%" not in ct.describe_evidence(locale, {"type": "accuracy", "skill": "grammar", "correct": 9, "total": 20, "days": 14})


def _scenarios():
    """Learner situations that between them make the rule engine emit every kind of evidence."""
    rich = LearnerStats(grammar_correct=9, grammar_total=20, weak_topics=[("了", 7), ("在", 4)], tasks_done_3d=1,
                        tasks_scheduled_3d=3)
    thin = LearnerStats(grammar_correct=1, grammar_total=2)
    plans = [PlanState(), PlanState(daily_minutes=60, sessions_per_day=1),
             PlanState(daily_minutes=10, new_grammar=1, exercise_count=3)]  # the last one is already at its lightest
    for reason, plan, stats, minutes in itertools.product(actions.REASONS, plans, (rich, thin), (None, 20)):
        yield reason, plan, stats, minutes


def test_everything_the_rule_engine_can_emit_is_described_in_every_locale():
    seen_types, seen_codes = set(), set()
    for reason, plan, stats, minutes in _scenarios():
        for item in build_items(reason, plan, stats, date(2026, 11, 18), minutes):
            seen_codes.add(item.action_code)
            for locale in LOCALES:
                clean(ct.describe_title(locale, item.action_code))
                clean(ct.describe_item(locale, item.action_code, item.params, plan.__dict__))
                for fact in item.evidence:
                    seen_types.add(fact["type"])
                    clean(ct.describe_evidence(locale, fact))
    assert seen_codes == set(actions.ACTION_CODES)
    assert seen_types == set(ct.EVIDENCE_TYPES)


# ----------------------------------------------------------------------------- reasons and summaries
@pytest.mark.parametrize("locale,reason", list(itertools.product(LOCALES, actions.REASONS)))
def test_every_reason_has_a_title_and_a_calm_summary(locale, reason):
    clean(ct.reason_title(locale, reason)), clean(ct.summarize(locale, reason))
    assert i18n.t(locale, f"coach.reason.{reason}.desc") != f"coach.reason.{reason}.desc"


def test_unknown_reason_falls_back_to_the_generic_summary():
    assert ct.summarize("en", "lazy") == i18n.t("en", "coach.summary.default")


@pytest.mark.parametrize("locale", LOCALES)
def test_summaries_never_claim_a_change_already_happened_or_blame_the_learner(locale):
    en = " ".join(i18n.t("en", f"coach.summary.{r}") for r in (*actions.REASONS, "default")).lower()
    assert "lazy" not in en and "you must" not in en and "should have" not in en
    for r in actions.REASONS:
        clean(ct.summarize(locale, r))


# ----------------------------------------------------------------------------- plan changes
CHANGES = {
    "daily_minutes": (30, 15), "new_grammar": (5, 2), "exercise_count": (8, 5), "sessions_per_day": (1, 2),
    "focus_minutes": (None, 10), "review_topics": ([], [["了", "2026-11-25"]]), "rest_until": (None, "2026-11-19"),
}


def test_every_plan_field_is_covered():
    assert set(CHANGES) == set(ct.FIELD_ORDER)


@pytest.mark.parametrize("locale,field", list(itertools.product(LOCALES, ct.FIELD_ORDER)))
def test_every_plan_field_change_reads_naturally(locale, field):
    old, new = CHANGES[field]
    text = ct.describe_change(locale, field, old, new)
    assert text.strip() and "{" not in text and "coach." not in text and "None" not in text and "?" not in text


@pytest.mark.parametrize("locale", LOCALES)
def test_numbers_and_topics_appear_in_the_change_sentence(locale):
    assert "30" in ct.describe_change(locale, "daily_minutes", 30, 15) and "15" in ct.describe_change(locale, "daily_minutes", 30, 15)
    assert "了" in ct.describe_change(locale, "review_topics", [], [{"topic": "了", "until": "2026-11-25"}])
    assert "了" in ct.describe_change(locale, "review_topics", [], (("了", "2026-11-25"),))  # audit rows hold tuples->lists
    assert i18n.t(locale, "coach.value.none") in ct.describe_change(locale, "focus_minutes", None, 10)


def test_dates_follow_each_languages_habit():
    d = date(2026, 11, 9)
    assert ct.format_date("vi", d) == "09/11/2026"
    assert ct.format_date("zh-TW", d) == "2026年11月9日"
    assert ct.format_date("en", "2026-11-09") == "2026-11-09"
    assert ct.format_date("en", "not a date") == "not a date"


def test_plan_change_lists_only_what_differs():
    old = {"daily_minutes": 30, "new_grammar": 5, "exercise_count": 8, "sessions_per_day": 1, "focus_minutes": None,
           "review_topics": []}
    new = {**old, "daily_minutes": 45, "exercise_count": 10}  # a manual edit stores the whole plan
    lines = ct.describe_plan_change("en", old, new)
    assert [ln["field"] for ln in lines] == ["daily_minutes", "exercise_count"]
    assert lines[0]["text"] == "Daily study time: 30 → 45 minutes"
    # a proposal stores only the changed fields, and rest_until is part of it
    lines = ct.describe_plan_change("en", {"rest_until": None}, {"rest_until": "2026-11-19"})
    assert lines == [{"field": "rest_until", "text": "Rest: your next study day is 2026-11-19"}]
    assert ct.describe_plan_change("en", {}, {}) == [] and ct.describe_plan_change("en", None, None) == []


# ----------------------------------------------------------------------------- the locale files themselves
def test_coach_namespace_has_identical_keys_and_placeholders_in_every_locale():
    keys = {loc: {k: v for k, v in i18n.catalog(loc).items() if k.startswith("coach.")} for loc in LOCALES}
    assert keys["en"], "coach.json missing"
    for loc in LOCALES:
        assert set(keys[loc]) == set(keys["en"]), (loc, sorted(set(keys["en"]) ^ set(keys[loc]))[:10])
        for key, text in keys[loc].items():
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", keys["en"][key])), (loc, key)
            assert "<" not in text and ">" not in text and text.strip(), (loc, key)


def test_traditional_chinese_only_and_no_mainland_only_characters_in_zh_tw():
    bad = {k: sorted(set(v) & SIMPLIFIED_ONLY) for k, v in i18n.catalog("zh-TW").items() if k.startswith("coach.") and set(v) & SIMPLIFIED_ONLY}
    assert not bad, bad


@pytest.mark.parametrize("locale", LOCALES)
def test_glance_is_built_from_the_items_and_empty_without_them(locale):
    text = ct.glance(locale, ["reduce_new_grammar", "take_rest"])
    clean(text)
    assert ct.describe_title(locale, "reduce_new_grammar") in text and ct.describe_title(locale, "take_rest") in text
    assert ct.glance(locale, []) == ""
