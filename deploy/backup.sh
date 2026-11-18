#!/usr/bin/env bash
# Consistent backup of the Huayu Coach database. Run it on the VPS host.
#
#   ./deploy/backup.sh      -> ~/huayu-backups/huayu-YYYYmmdd-HHMMSS.db.gz   (keeps the newest 14 FILES, deletes older ones)
#
# Daily at 03:15 with cron (crontab -e). Only errors go to the mail (stderr), normal output goes to the log:
#   MAILTO=you@example.com
#   15 3 * * * cd /home/deploy/huayu-coach && ./deploy/backup.sh >> /home/deploy/huayu-backups/backup.log
# MAILTO works only if the server has a mail transfer agent, which a fresh VPS usually does not. The reliable alert is BACKUP_PING_URL.
#
# Variables (all optional):
#   BACKUP_DIR       where the backups go (default ~/huayu-backups; a path with spaces is fine)
#   KEEP             how many backup files to keep (default 14). With one backup a day that is 14 DAYS. If you run it more often,
#                    the same 14 files cover fewer days. The privacy page says 14 days: change it there if you change KEEP.
#   BACKUP_PING_URL  OPTIONAL dead-man's-switch (healthchecks.io style). The URL is requested after every successful backup
#                    and "<url>/fail" after a failed one. If the pings stop coming, the service e-mails you. A failed ping never
#                    fails the backup.
#   BACKUP_MODE      docker (default: the Docker Compose install) or host (the no-Docker install, deploy/huayu-coach.service).
#                    In host mode run it as root (sudo crontab -e): it runs the helper as the service user, so SQLite files
#                    are never created owned by the wrong user. HUAYU_DATA_DIR (default /var/lib/huayu-coach) and
#                    HUAYU_USER (default huayu) can be set. Needs python3 on the host.
#
# The backups contain every learner's personal data: they are created readable by you only. Copy them off the server too.
# The backup does NOT contain .env (the signing key and SMTP password): keep a copy of that file somewhere safe as well.
set -euo pipefail
umask 077

here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/huayu-backups}"
KEEP="${KEEP:-14}"
BACKUP_MODE="${BACKUP_MODE:-docker}"
ping_url="${BACKUP_PING_URL:-}"

case "$KEEP" in
  ''|*[!0-9]*) echo "KEEP must be a whole number, got: '$KEEP'" >&2; exit 2 ;;
esac
KEEP=$((10#$KEEP))  # "08" must not be read as octal
if [ "$KEEP" -lt 1 ]; then
  echo "KEEP must be at least 1 (0 would delete the backup that was just made)" >&2
  exit 2
fi

# Never lets a monitoring problem break the backup itself.
ping_monitor() {
  if [ -n "$ping_url" ] && command -v curl >/dev/null 2>&1; then
    curl -fsS -m 10 --retry 3 -o /dev/null "$1" || echo "warning: could not reach the backup monitor" >&2
  fi
  return 0
}

mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/huayu-$(date +%Y%m%d-%H%M%S).db.gz"
partial="$out.partial"

on_exit() {
  status=$?
  rm -f "$partial"
  if [ "$status" -ne 0 ]; then
    echo "$(date +%Y-%m-%dT%H:%M:%S%z) BACKUP FAILED (exit $status)" >&2
    ping_monitor "${ping_url%/}/fail"
  fi
}
trap on_exit EXIT

# backup_db.py uses the SQLite backup API (safe while the app is serving) and prints the finished copy on stdout.
case "$BACKUP_MODE" in
  docker)
    docker compose exec -T app python - < "$here/backup_db.py" | gzip -9 > "$partial"
    ;;
  host)
    sudo -n -u "${HUAYU_USER:-huayu}" env HUAYU_DATA_DIR="${HUAYU_DATA_DIR:-/var/lib/huayu-coach}" \
      python3 "$here/backup_db.py" | gzip -9 > "$partial"
    ;;
  *)
    echo "BACKUP_MODE must be docker or host, got: '$BACKUP_MODE'" >&2
    exit 2
    ;;
esac
gzip -t "$partial"

# The file must really be an SQLite database, not an empty or truncated stream. (The reader drains the rest of the pipe so
# that gzip does not die of SIGPIPE under `pipefail`; 15 bytes because a shell variable cannot hold the NUL after the magic.)
magic="$(gzip -dc < "$partial" | { head -c 15; cat > /dev/null; })"
if [ "$magic" != "SQLite format 3" ]; then
  echo "the backup is not an SQLite database; it was discarded" >&2
  exit 1
fi

mv "$partial" "$out"
echo "$(date +%Y-%m-%dT%H:%M:%S%z) backup written: $out ($(du -h "$out" | cut -f1))"

# Keep only the newest $KEEP backups. The names hold the timestamp, so alphabetical order is time order, and the pattern
# matches only files this script made (a file you copied in by hand is never deleted). The glob is quoted-safe for spaces.
backups=("$BACKUP_DIR"/huayu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].db.gz)
extra=$(( ${#backups[@]} - KEEP ))
if [ "$extra" -gt 0 ]; then
  for old in "${backups[@]:0:$extra}"; do
    rm -f -- "$old"
    echo "deleted old backup: $old"
  done
fi

ping_monitor "$ping_url"
