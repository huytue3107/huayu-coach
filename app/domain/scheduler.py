"""Builds one day's task from the plan. Pure function: no database, deterministic for (user, day).

`plan.new_grammar` = how many first-time grammar questions today; `plan.exercise_count` = total questions.
The rest of the slots go, in order, to: topics the learner asked to review, questions answered wrong
before, unseen vocab/reading, and finally the oldest already-correct ones (so a small bank never runs dry).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class QInfo:
    id: str
    skill: str
    topic: str | None
    level: str


@dataclass(frozen=True)
class History:
    """What this learner did with a question so far."""
    attempts: int = 0
    last_correct: bool | None = None
    last_seen: str = ""  # iso timestamp, "" if never


def pick_questions(pool: list[QInfo], history: dict[str, History], exercise_count: int, new_grammar: int,
                   review_topics: list[str], user_id: int, day: date) -> list[str]:
    rng = random.Random(f"{user_id}:{day.isoformat()}")
    chosen: list[str] = []

    def take(candidates: list[QInfo], limit: int) -> None:
        cands = [q for q in candidates if q.id not in chosen]
        rng.shuffle(cands)
        for q in cands[: max(0, min(limit, exercise_count - len(chosen)))]:
            chosen.append(q.id)

    h = lambda q: history.get(q.id, History())  # noqa: E731
    unseen = [q for q in pool if h(q).attempts == 0]
    wrong = [q for q in pool if h(q).last_correct is False]

    review = [q for q in pool if q.topic in review_topics and h(q).last_correct is not True] or \
             [q for q in pool if q.topic in review_topics]
    take(review, 2)
    take(wrong, 2)
    take([q for q in unseen if q.skill == "grammar"], new_grammar)
    take([q for q in unseen if q.skill != "grammar"], exercise_count)
    take(unseen, exercise_count)
    older_first = sorted([q for q in pool if q.id not in chosen], key=lambda q: (h(q).last_seen, q.id))
    for q in older_first:
        if len(chosen) >= exercise_count:
            break
        chosen.append(q.id)
    return chosen
