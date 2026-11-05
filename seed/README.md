# seed/

* `auto_rules.json` — automatic evaluation rules, keyed by evaluation criterion name.
  At startup they are attached to criteria without a rule whose questionnaire contains
  every question code the rule uses.
* `manifest.json` — Word questionnaires to import automatically at startup (each once).
  Empty since 2026-11-05: the questionnaire is already in use, and the unused copies
  were removed. To import a new questionnaire, put the .docx here and add
  `{"file": "name.docx", "label": "…", "language": "tg"}` to the list.
* `01-…docx`, `02-…docx` — the original and the corrected questionnaire (reference copies).
