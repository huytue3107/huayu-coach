#!/usr/bin/env bash
# Restore a backup made by backup.sh (Docker install). Run it on the VPS host.
#
#   ./deploy/restore.sh ~/huayu-backups/huayu-20260101-031500.db.gz
#
# It REPLACES the live database. The current one is first copied aside inside the data volume as
# huayu.db.before-restore-<time> (a full copy of every learner's data: delete it once the restore is confirmed, see
# deploy/deploy.md), and the app is stopped while the swap happens (a few seconds of downtime).
# On start the app applies any newer database migrations itself, so a backup from an older version is fine.
#
# Safety: a missing, empty, non-gzip or non-SQLite file is refused BEFORE the app is stopped. restore_db.py then checks the
# database again inside the container (integrity_check, Huayu tables) before replacing anything. Whatever happens after
# the app has been stopped, the script starts it again before it exits.
#
# A restore also brings back every account that was deleted after that backup was made. See "Retention and deleted accounts"
# in deploy/deploy.md: re-apply the deletion requests you received since.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

file="${1:-}"
if [ -z "$file" ] || [ ! -f "$file" ]; then
  echo "Usage: $0 <backup file, e.g. ~/huayu-backups/huayu-20260101-031500.db.gz>" >&2
  exit 1
fi
# Make the path absolute BEFORE changing directory, so a relative name given from the backups folder still works.
file="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"
cd "$here/.."

if [ ! -s "$file" ]; then
  echo "Refused: $file is empty. Nothing was changed." >&2
  exit 1
fi
if ! gzip -t "$file" 2>/dev/null; then
  echo "Refused: $file is not a valid .gz file (truncated or the wrong file). Nothing was changed." >&2
  exit 1
fi
magic="$(gzip -dc < "$file" | { head -c 15; cat > /dev/null; })"
if [ "$magic" != "SQLite format 3" ]; then
  echo "Refused: $file does not contain an SQLite database. Nothing was changed." >&2
  exit 1
fi

echo "This will REPLACE the live database with: $file"
echo "Everything learners did after that backup was made will be lost (a copy of the current database is kept aside),"
echo "and accounts deleted since then will come back."
read -r -p "Type RESTORE to continue: " answer
if [ "$answer" != "RESTORE" ]; then
  echo "Cancelled. Nothing was changed."
  exit 1
fi

# From here on the site is down, so whatever happens the app is started again.
phase=running   # running -> stopped (app is down) -> restored (database replaced, app being started)
on_exit() {
  status=$?
  if [ "$phase" = stopped ]; then
    echo "Starting the app again..." >&2
    if ! docker compose up -d app; then
      echo "COULD NOT START THE APP. The site is down. Run: docker compose up -d app   then: docker compose logs --tail 50 app" >&2
    fi
    echo "The restore did NOT complete (exit $status). If the message above says 'refused', the live database was not replaced and the app runs on it again." >&2
  elif [ "$phase" = restored ] && [ "$status" -ne 0 ]; then
    echo "The database WAS replaced, but the app did not become healthy. See: docker compose logs --tail 50 app" >&2
    echo "The previous database is in the data volume as huayu.db.before-restore-*; see deploy/deploy.md section 12 to go back." >&2
  fi
}
trap on_exit EXIT

docker compose stop app
phase=stopped
# restore_db.py reads the database from stdin; the one-off container replaces the normal start command.
gunzip -c "$file" | docker compose run --rm --no-deps -T app python -c "$(cat "$here/restore_db.py")"
phase=restored
docker compose up -d --wait app
echo "Done. Check https://YOUR-DOMAIN/healthz and log in."
echo "Reminder 1: accounts deleted after this backup was made are back. Re-apply the deletion requests you received since."
echo "Reminder 2: the database you replaced was kept as huayu.db.before-restore-* in the data volume. It holds personal data: delete it when you are sure (deploy/deploy.md, section 10)."
