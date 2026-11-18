"""Command line: `python -m app.cli <command>`.

  prepare        apply database migrations + load reference data (idempotent; run on every start/deploy)
  create-admin   create an administrator, or promote an existing account WITHOUT changing its password (--reset-password to change it)
  demo           DEV ONLY: sample learner/teacher/admin with study history so every screen has something to show
  check          print the effective configuration (secrets hidden) and verify the database is reachable
  delete-user    erase an account on request (same code path as the in-app delete button)
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from . import db as dbmod
from .config import ROOT, get_settings
from .models import Question, QuizAttempt, StudyTask, User
from .security import hash_password
from .services import accounts, billing, coach, consent, plans, seed
from .timeutil import local_today, utcnow


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def cmd_prepare(_: argparse.Namespace) -> int:
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(), "head")
    dbmod.init_db()
    with dbmod.write_session() as s:
        added = seed.seed_all(s)
    print(f"database ready ({get_settings().db_url.split('///')[0]}); seeded {added}")
    for warning in config_warnings():
        print("WARNING:", warning)
    return 0


def config_warnings() -> list[str]:
    """Things that are legal or operational risks for a real launch but not reasons to refuse to start."""
    s = get_settings()
    out: list[str] = []
    if s.env == "prod":
        if not s.support_email:
            out.append("HUAYU_SUPPORT_EMAIL is empty: the privacy page has no contact address for data requests.")
        if not s.smtp_host:
            out.append("HUAYU_SMTP_HOST is empty: password-reset e-mails are only written to data/outbox and never delivered.")
        if s.allow_unreviewed_questions is False:
            with dbmod.read_session() as db:
                approved = db.scalar(select(func.count()).select_from(Question).where(Question.review_status == "approved")) or 0
            if approved == 0:
                out.append("No question is approved and unreviewed questions are not allowed: learners will see no exercises "
                           "until a teacher approves questions in /admin/questions.")
    return out


def _read_password(args: argparse.Namespace) -> str:
    pw = args.password or os.environ.get("HUAYU_ADMIN_PASSWORD")
    if not pw:
        pw = getpass.getpass("Password (min %d chars): " % get_settings().min_password_length)
        if getpass.getpass("Repeat: ") != pw:
            sys.exit("Passwords differ.")
    if len(pw) < get_settings().min_password_length:
        sys.exit(f"Password must be at least {get_settings().min_password_length} characters.")
    return pw


def cmd_create_admin(args: argparse.Namespace) -> int:
    """New e-mail: create the admin (asks for a password). Existing account: promote it and leave its password alone,
    unless --reset-password is given (a promotion must not lock the person out or hand them a different password)."""
    email = args.email.strip().lower()
    dbmod.init_db()
    with dbmod.read_session() as s:
        exists = s.scalar(select(User.id).where(User.email == email)) is not None
    pw = _read_password(args) if (not exists or args.reset_password) else None  # prompt BEFORE taking the write lock
    with dbmod.write_session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user:
            user.role = "admin"
            if pw:
                user.password_hash = hash_password(pw)
                user.session_version += 1  # a new password logs the person out everywhere
            print(f"promoted existing account {email} to admin" + (" and set a new password" if pw else " (password unchanged)"))
        else:
            s.add(User(email=email, password_hash=hash_password(pw), role="admin", locale=get_settings().default_locale,
                       onboarded_at=utcnow()))
            print(f"created admin {email}")
    return 0


def cmd_delete_user(args: argparse.Namespace) -> int:
    """Erase an account on request (e.g. a student e-mailed the operator). Same code path as the in-app button."""
    email = args.email.strip().lower()
    dbmod.init_db()
    with dbmod.write_session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"No account with e-mail {email}.")
        if accounts.is_last_admin(s, user):
            sys.exit("Refusing to delete the only administrator. Create another admin first.")
        if not args.yes and input(f"Permanently delete {user.public_id} ({user.role}) and all of their data? Type DELETE: ").strip() != "DELETE":
            sys.exit("Cancelled.")
        accounts.erase_account(s, user)
    print(f"deleted {email}. Backups taken before now may still contain the account until they expire (see deploy/deploy.md).")
    return 0


DEMO_PASSWORD = "demo-password-1"


def cmd_demo(_: argparse.Namespace) -> int:
    if get_settings().env == "prod":
        sys.exit("demo data is for development only (HUAYU_ENV=prod).")
    cmd_prepare(_)
    dbmod.init_db()
    now = utcnow()
    with dbmod.write_session() as s:
        for email, role in (("admin@huayu.local", "admin"), ("teacher@huayu.local", "teacher")):
            if not s.scalar(select(User).where(User.email == email)):
                s.add(User(email=email, password_hash=hash_password(DEMO_PASSWORD), role=role, locale="vi", onboarded_at=now))
        if s.scalar(select(User).where(User.email == "learner@huayu.local")):
            print("demo learner already exists")
            return 0
        u = User(email="learner@huayu.local", password_hash=hash_password(DEMO_PASSWORD), locale="vi", display_name="Mỹ Hạnh",
                 level="A1", onboarded_at=now - timedelta(days=9), pretest_done_at=now - timedelta(days=9))
        s.add(u)
        s.flush()
        plans.create_default_plan(s, u, 30)
        consent.record(s, u, llm_transfer_agreed=False)
        billing.start_trial(s, u)
        grammar = list(s.scalars(select(Question).where(Question.skill == "grammar", Question.stage == "daily")))
        today = local_today(u.timezone)
        # 9 days of study that leave a believable picture: grammar is weak, mostly on 了/完/在, completion is patchy
        n = 0
        for day in range(9, 0, -1):
            when = now - timedelta(days=day)
            if day in (5, 4, 3):
                continue  # a gap: the days the learner stopped
            task = StudyTask(user_id=u.id, task_date=today - timedelta(days=day), day_no=10 - day, question_ids=[],
                             planned_minutes=30, plan_version=1, status="done" if day > 5 else "pending", created_at=when)
            s.add(task)
            for q in grammar[: 6 + (day % 3)]:
                n += 1
                ok = (n % 5 == 0) or (q.topic not in ("了", "完", "在") and n % 2 == 0)
                s.add(QuizAttempt(user_id=u.id, question_id=q.id, stage="daily", selected_text=q.options[q.correct_index if ok else (q.correct_index + 1) % 4],
                                  is_correct=ok, skill=q.skill, topic=q.topic, created_at=when))
    print("demo accounts (password for all: %s): learner@huayu.local, teacher@huayu.local, admin@huayu.local" % DEMO_PASSWORD)
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    s = get_settings()
    print(f"env={s.env} base_url={s.base_url} db={s.db_url.split('///')[0]} billing_enabled={s.billing_enabled} "
          f"llm={s.llm_provider} smtp={'yes' if s.smtp_host else 'no (mail goes to data/outbox)'} "
          f"unreviewed_questions={s.allow_unreviewed_questions} registration_open={s.registration_open} invite_code={'set' if s.invite_code else 'no'}")
    from sqlalchemy import text
    dbmod.init_db()
    with dbmod.read_session() as db:
        db.execute(text("SELECT 1"))
    print("database reachable")
    for warning in config_warnings():
        print("WARNING:", warning)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--password", help="avoid: it stays in your shell history. Prefer the prompt or HUAYU_ADMIN_PASSWORD")
    a.add_argument("--reset-password", action="store_true", help="for an EXISTING account: also set a new password")
    a.set_defaults(fn=cmd_create_admin)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    d = sub.add_parser("delete-user")
    d.add_argument("--email", required=True)
    d.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    d.set_defaults(fn=cmd_delete_user)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
