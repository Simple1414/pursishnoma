## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

### Claude Code add-ons (setup per machine)

- **Ponytail** and **Agent Skills** (addyosmani) are enabled in `.claude/settings.json`; Claude Code installs them from their marketplaces on first trust of this folder (Ponytail needs Node.js on PATH).
- **Graphify** hooks in `.claude/settings.json` call the `graphify` CLI. Install it once and build the graph locally (it is gitignored):
  `pip install graphifyy && graphify update .`
- **Design plugins** enabled in `.claude/settings.json`: UI UX Pro Max (needs Python), Taste (needs Node 22+ and Chrome/Chromium), Impeccable (runs a design-check hook on UI edits; downloads its engine to `~/.impeccable/bin/` on first use; turn telemetry off with `DO_NOT_TRACK=1`), PPT Master (run `pip install -r requirements.txt` in its plugin folder before generating decks).
- **Archify** (diagrams) is gitignored because of its size; `skills-lock.json` records it. Restore it with:
  `npx -y skills@latest add tt-a1i/archify --skill archify --agent claude-code --copy --yes`
