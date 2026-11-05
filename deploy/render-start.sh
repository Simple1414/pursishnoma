#!/usr/bin/env bash
# Start step: apply migrations, ensure the first admin exists, run gunicorn.
set -o errexit
python manage.py migrate --noinput
python manage.py ensure_admin
python manage.py seed_questionnaires
python manage.py auto_evaluate
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --worker-class gthread --threads "${GUNICORN_THREADS:-4}" \
  --timeout 120 \
  --access-logfile - --error-logfile -
