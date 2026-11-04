# Пурсишнома · ResearchSurvey

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Simple1414/pursishnoma)

Платформа барои таҳия ва гузаронидани пурсишҳои илмӣ, арзёбии иштирокчиён, таҳлили оморӣ ва
тайёр кардани ҳисобот. Интерфейс бо забонҳои тоҷикӣ, русӣ ва англисӣ.

**Ба интернет гузоштан:** тугмаи «Deploy to Render»-ро пахш кунед → номи администратор ва рамзро
ворид кунед → **Apply**. Дастури пурра: [DEPLOYMENT.md](DEPLOYMENT.md).

---

**Intelligent Platform for Scientific Research and Survey Analytics** — create scientific
questionnaires (or import them from Word), collect responses, evaluate each participant,
run statistical analysis and export reports. UI in Tajik (default), Russian and English.

## Stack
Python 3.12+ · Django 5.2 · Django REST Framework · **PostgreSQL only** · Tailwind CSS (pre-built) ·
Alpine.js · Chart.js · pandas / NumPy / SciPy · openpyxl · python-docx · ReportLab · matplotlib ·
gunicorn + nginx (or Render.com). All front-end assets are vendored — no CDN, strict CSP.

## Features
| Area | What it does |
|---|---|
| Word import | `.docx` → sections, numbered questions, options, “other” fields, Likert grids with legend labels, required flags, participant fields, consent box. Wording is copied verbatim; result is a **draft** that must be approved before publication. Researcher-only appendices are skipped. |
| Survey builder | 11 question types (single, multiple, yes/no, Likert, rating, short/long text, number, date, matrix, ranking), sections, reordering, duplication, schedule, activate/close, public link + QR, preview. Structure locks once answers exist. |
| Participation | Public URL, no account. Consent, configurable optional participant fields, one section per step, progress bar, server autosave on every step, offline banner, idempotent submit, confirmation ID. |
| Responses | Unique IDs (`R-000042`), search/filter/sort, compare 2–4 participants, print, Word/PDF/Excel per response. Completed answers are immutable (enforced in the model). |
| Evaluation | Criteria with max score and weight, weighted final score (0–100 %), comments, recommendations, review status, full version history — stored separately from answers. |
| Statistics | Frequencies, %, mean, median, mode, min/max, SD, variance, N/n/missing on every item; cross-tabs with χ²/Fisher + Cramér’s V; group comparison (Mann–Whitney / Kruskal–Wallis, refused for n < 5); Spearman correlation; section means; interactive, exportable charts. |
| Research Intelligence | Data quality (speeders, straight-lining, shared network), missing data, trend, open-ended analysis (terms, bigrams, similar-answer themes, heuristic tendencies, problem mentions — all linked to response IDs), rule-based findings with evidence, draft conclusions from approved findings only. |
| AI assistant (optional) | Anthropic Claude via official SDK; sends aggregates + open answers with IDs (never names); output stored as a draft, editable, approve/reject; the data sent is visible. Platform works fully without it. |
| Reports & export | Editable scientific report (objectives → recommendations) with auto tables/charts → Word/PDF; Excel (7 sheets incl. codebook), CSV (labels/codes); export history + audit log; formula-injection safe. |
| Security | Roles (admin/researcher) with object-level checks, PBKDF2, CSRF, POST-only mutations, rate limiting (login/start/submit), upload validation (extension + magic bytes + size + zip-bomb guard, randomised names), CSP, HSTS/secure cookies, audit log. |

## Local development
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb researchsurvey                     # PostgreSQL must be running
cp .env.example .env                        # set DATABASE_URL, DJANGO_DEBUG=true, DJANGO_USE_HTTPS=false
python manage.py migrate
DJANGO_ADMIN_USERNAME=admin DJANGO_ADMIN_PASSWORD='…' python manage.py ensure_admin
python manage.py runserver
```
CSS is pre-built in `static/css/app.css`. After changing templates/classes:
`cd frontend && npm install && npm run build` (copies Alpine/Chart.js/fonts and rebuilds Tailwind).

Translations: `locale/tg`, `locale/ru` (`.po` files are editable; `python manage.py compilemessages`).

## Tests
```bash
DJANGO_FAST_PASSWORD_HASHER=1 python manage.py test      # runs against PostgreSQL
```
93 tests cover authentication, role × view access matrix and IDOR, Word import, builder, public
submission (all question types, required/invalid/duplicate/closed), immutability, evaluation,
statistics (verified against NumPy/SciPy), text analysis, AI integration (mocked), reports and exports.

## Project layout
`config/` settings & URLs · `core/` users, audit, site settings, dashboard, admin pages ·
`surveys/` questionnaires, importer, builder, public flow, API · `evaluation/` · `analytics/`
(stats, text, intelligence, AI) · `reports/` (reports, exports) · `templates/` · `static/` ·
`frontend/` (Tailwind build) · `deploy/` (Render, gunicorn, systemd, nginx, backups).
