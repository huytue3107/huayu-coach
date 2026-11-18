"""Consistent online backup of the SQLite database. Runs INSIDE the app container (deploy/backup.sh pipes it in).

Uses SQLite's backup API, so it is safe while the app is serving requests (a plain `cp` of a WAL database is not).
The finished copy is checked with PRAGMA integrity_check and written to stdout; the caller compresses it.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

src = os.path.join(os.environ.get("HUAYU_DATA_DIR", "/data"), "huayu.db")
if not os.path.exists(src):
    sys.exit(f"no database at {src}")

workdir = tempfile.mkdtemp(prefix="huayu-backup-")
try:
    copy = os.path.join(workdir, "copy.db")
    source = sqlite3.connect(src, timeout=30)
    target = sqlite3.connect(copy)
    source.backup(target)
    source.close()
    verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
    target.close()
    if verdict != "ok":
        sys.exit(f"integrity_check failed: {verdict}")
    with open(copy, "rb") as fh:
        shutil.copyfileobj(fh, sys.stdout.buffer)
    sys.stdout.buffer.flush()
finally:
    shutil.rmtree(workdir, ignore_errors=True)
