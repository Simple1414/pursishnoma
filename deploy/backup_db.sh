#!/usr/bin/env bash
# Daily PostgreSQL backup with rotation. Cron example (02:30 every day):
#   30 2 * * * /srv/researchsurvey/app/deploy/backup_db.sh >> /var/log/researchsurvey-backup.log 2>&1
# Reads DATABASE_URL from the environment or from the project's .env file.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${DATABASE_URL:-}" && -f .env ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
fi
: "${DATABASE_URL:?DATABASE_URL is not set}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/researchsurvey}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
FILE="$BACKUP_DIR/researchsurvey-$(date +%Y%m%d-%H%M%S).dump"
pg_dump --format=custom --no-owner --file="$FILE" "$DATABASE_URL"
chmod 600 "$FILE"
pg_restore --list "$FILE" > /dev/null   # verify the archive is readable
find "$BACKUP_DIR" -name 'researchsurvey-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
