"""Time helpers. All instants are stored as UTC; study days are computed in the learner's timezone."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(tz: str = "Asia/Taipei", now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(ZoneInfo(tz)).date()


def local_midnight_utc(day: date, tz: str = "Asia/Taipei") -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def days_ago(n: int, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=n)
