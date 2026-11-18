"""Loads reference data. Safe to run repeatedly: existing rows are never overwritten (a teacher's edits survive)."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import ROOT
from ..models import Question
from . import billing

SEED_DIR = ROOT / "seeds"


def load_questions(db: Session, path: Path | None = None) -> int:
    path = path or SEED_DIR / "questions.json"
    added = 0
    for q in json.loads(path.read_text(encoding="utf-8")):
        if db.get(Question, q["id"]) is None:
            db.add(Question(**q))
            added += 1
    return added


def seed_all(db: Session) -> dict[str, int]:
    return {"questions": load_questions(db), "billing_plans": billing.seed_plans(db)}
