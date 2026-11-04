#!/usr/bin/env bash
# Restore a backup into the database in DATABASE_URL.
# WARNING: replaces existing objects. Stop the app first:  sudo systemctl stop researchsurvey
# Usage: deploy/restore_db.sh /var/backups/researchsurvey/researchsurvey-YYYYmmdd-HHMMSS.dump
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: restore_db.sh <backup.dump>}"
if [[ -z "${DATABASE_URL:-}" && -f .env ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
fi
: "${DATABASE_URL:?DATABASE_URL is not set}"
read -r -p "Restore $FILE into the configured database? Type YES: " answer
[[ "$answer" == "YES" ]] || { echo "aborted"; exit 1; }
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$FILE"
echo "restore complete"
