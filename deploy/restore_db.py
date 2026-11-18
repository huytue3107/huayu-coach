"""Replaces the live SQLite database with a backup read from stdin. Runs INSIDE a one-off app container with the app stopped
(deploy/restore.sh does that), or on the host for the no-Docker install (see deploy/deploy.md). The current database is first
copied aside as huayu.db.before-restore-<time>, never deleted by this script.

The incoming file is checked BEFORE anything is touched: it must be non-empty, an SQLite file, pass integrity_check and look
like a Huayu Coach database (it has the `users` and `alembic_version` tables, and a migration revision). Any other file,
including a valid SQLite file from another program, is refused and the live database stays exactly as it was.
"""
import os
import shutil
import sqlite3
import sys
import time

REQUIRED_TABLES = ("alembic_version", "users")

data = os.environ.get("HUAYU_DATA_DIR", "/data")
live = os.path.join(data, "huayu.db")
incoming = live + ".restoring"


def refuse(reason):
    sys.exit(f"refused: {reason}; nothing was changed")


def cleanup():
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(incoming + suffix):
            os.unlink(incoming + suffix)


try:
    fd = os.open(incoming, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(sys.stdin.buffer, out)

    if os.path.getsize(incoming) == 0:
        refuse("the backup is empty (0 bytes)")
    with open(incoming, "rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            refuse("the file is not an SQLite database (wrong file, or a truncated or corrupted copy)")

    try:
        check = sqlite3.connect(incoming)
        try:
            verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            revision = check.execute("SELECT version_num FROM alembic_version").fetchone() if "alembic_version" in tables else None
        finally:
            check.close()
    except sqlite3.DatabaseError as exc:
        verdict, tables, revision = str(exc), set(), None
    if verdict != "ok":
        refuse(f"the backup failed integrity_check ({verdict})")
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    if missing:
        refuse(f"this is an SQLite file but not a Huayu Coach database (missing table: {', '.join(missing)})")
    if not revision or not revision[0]:
        refuse("the database has no migration revision (alembic_version is empty)")

    if os.path.exists(live):
        aside = f"{live}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
        old, saved = sqlite3.connect(live, timeout=30), sqlite3.connect(aside)
        old.backup(saved)
        old.close()
        saved.close()
        os.chmod(aside, 0o600)
        print("previous database kept as", aside)

    for suffix in ("-wal", "-shm"):  # leftovers of the old database must not be paired with the restored file
        if os.path.exists(live + suffix):
            os.unlink(live + suffix)
    os.replace(incoming, live)
    print("restored", live, f"(migration revision {revision[0]})")
finally:
    cleanup()
