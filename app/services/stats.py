"""What the learner's own answers and tasks say. These numbers are computed, never typed in by hand."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.diagnosis import LearnerStats
from ..models import QuizAttempt, StudyTask, User
from ..timeutil import local_today, utcnow


def learner_stats(db: Session, user: User, now: datetime | None = None, window_days: int = 14) -> LearnerStats:
    now = now or utcnow()
    since = now - timedelta(days=window_days)
    rows = db.execute(select(QuizAttempt.skill, QuizAttempt.topic, QuizAttempt.is_correct)
                      .where(QuizAttempt.user_id == user.id, QuizAttempt.created_at >= since)).all()
    stats = LearnerStats(window_days=window_days)
    wrong_topics: Counter[str] = Counter()
    for skill, topic, ok in rows:
        if skill == "grammar":
            stats.grammar_total += 1
            stats.grammar_correct += int(ok)
        else:
            stats.vocab_total += 1
            stats.vocab_correct += int(ok)
        if not ok and topic:
            wrong_topics[topic] += 1
    stats.weak_topics = wrong_topics.most_common(3)

    today = local_today(user.timezone, now)
    tasks = db.execute(select(StudyTask.status).where(
        StudyTask.user_id == user.id, StudyTask.task_date >= today - timedelta(days=3), StudyTask.task_date < today)).scalars().all()
    stats.tasks_scheduled_3d = len(tasks)
    stats.tasks_done_3d = sum(1 for s in tasks if s == "done")
    return stats
