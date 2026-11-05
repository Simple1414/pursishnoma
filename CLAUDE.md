# CLAUDE.md — Пурсишнома (ResearchSurvey)

Django 5.2 + Django REST Framework survey platform; frontend in `frontend/` (Tailwind). See `README.md` and `DEPLOYMENT.md`.

## Claude Code setup

- **Plugins** (`.claude/settings.json`): superpowers, context7, pyright-lsp, frontend-design (official marketplace); ui-ux-pro-max (offline UI design database); ponytail (keeps diffs minimal; needs Node.js). Use the built-in `/code-review` and `/simplify` for review.
- **Archify** (diagrams) is gitignored because of its 18 MB size; `skills-lock.json` pins it. Restore with `npx -y skills@latest add tt-a1i/archify --skill archify --agent claude-code --copy --yes`.
- Keep this set lean: every plugin adds skill descriptions or hooks to each session. Add one only when it fills a gap the set above does not.
