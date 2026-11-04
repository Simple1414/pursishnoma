#!/usr/bin/env bash
# Build step for Render (and any PaaS): install deps and collect static files.
set -o errexit
pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
