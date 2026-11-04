"""Tabular datasets built from PostgreSQL data for exports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd
from django.utils.translation import gettext as _

from analytics import stats
from evaluation.models import Evaluation, EvaluationCriterion
from surveys.models import Question, SurveyResponse

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in user-provided strings."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _option_code(option) -> Any:
    return option.value if option.value is not None else option.text


def wide_dataset(ds: stats.Dataset, *, labels: bool) -> pd.DataFrame:
    """One row per response; one or more columns per question."""
    rows: dict[int, dict[str, Any]] = {}
    for rid, r in ds.responses.items():
        p = r.participant
        rows[rid] = {
            "response_id": r.reference,
            "status": r.get_status_display(),
            "started_at": r.started_at.replace(tzinfo=None) if r.started_at else None,
            "submitted_at": r.submitted_at.replace(tzinfo=None) if r.submitted_at else None,
            "duration_sec": r.duration_seconds,
            "consent": int(p.consent_given),
        }
        for name in ("name", "age", "gender", "education_level", "university", "faculty", "specialty", "academic_year", "group_name"):
            rows[rid][f"p_{name}"] = getattr(p, name) or None
        for k, v in (p.extra or {}).items():
            rows[rid][f"p_{k}"] = v
    columns: list[str] = []
    for q in ds.questions:
        answers = ds.answers.get(q.pk, [])
        if q.qtype == Question.Type.MULTIPLE:
            for o in q.choice_options():
                col = f"{q.code}_{_option_code(o)}"
                columns.append(col)
                for rid in rows:
                    rows[rid][col] = 0
            for a in answers:
                if a.option_id and a.response_id in rows:
                    rows[a.response_id][f"{q.code}_{_option_code(a.option)}"] = 1
                    if a.text_value:
                        rows[a.response_id][f"{q.code}_{_option_code(a.option)}_text"] = a.text_value
        elif q.qtype in (Question.Type.SINGLE, Question.Type.YES_NO, Question.Type.LIKERT):
            columns.append(q.code)
            for a in answers:
                if a.option_id and a.response_id in rows:
                    rows[a.response_id][q.code] = a.option.text if labels else _option_code(a.option)
                    if a.text_value:
                        rows[a.response_id][f"{q.code}_text"] = a.text_value
        elif q.qtype == Question.Type.MATRIX:
            for i, row in enumerate(q.matrix_rows(), start=1):
                col = f"{q.code}_r{i}"
                columns.append(col)
            row_index = {row.pk: i for i, row in enumerate(q.matrix_rows(), start=1)}
            for a in answers:
                if a.row_id and a.response_id in rows:
                    rows[a.response_id][f"{q.code}_r{row_index.get(a.row_id, 0)}"] = a.option.text if labels else _option_code(a.option)
        elif q.qtype == Question.Type.RANKING:
            for o in q.choice_options():
                columns.append(f"{q.code}_{_option_code(o)}_rank")
            for a in answers:
                if a.option_id and a.response_id in rows:
                    rows[a.response_id][f"{q.code}_{_option_code(a.option)}_rank"] = a.rank
        else:
            columns.append(q.code)
            for a in answers:
                if a.response_id not in rows:
                    continue
                if a.number_value is not None:
                    rows[a.response_id][q.code] = float(a.number_value)
                elif a.date_value:
                    rows[a.response_id][q.code] = a.date_value
                else:
                    rows[a.response_id][q.code] = a.text_value
    frame = pd.DataFrame.from_dict(rows, orient="index")
    for col in columns:
        if col not in frame.columns:
            frame[col] = None
    frame = frame.map(safe_cell) if not frame.empty else frame
    return frame.reset_index(drop=True)


def answers_long(ds: stats.Dataset) -> pd.DataFrame:
    records = []
    qmap = {q.pk: q for q in ds.questions}
    for qid, answers in ds.answers.items():
        q = qmap.get(qid)
        if q is None:
            continue
        for a in answers:
            r = ds.responses.get(a.response_id)
            if r is None:
                continue
            records.append(
                {
                    "response_id": r.reference,
                    "question_number": q.number,
                    "question_code": q.code,
                    "question": q.text,
                    "type": q.get_qtype_display(),
                    "row": a.row.text if a.row_id else None,
                    "option": a.option.text if a.option_id else None,
                    "option_code": a.option.value if a.option_id else None,
                    "rank": a.rank,
                    "number": float(a.number_value) if a.number_value is not None else None,
                    "date": a.date_value,
                    "text": a.text_value or None,
                }
            )
    frame = pd.DataFrame.from_records(records)
    return frame.map(safe_cell) if not frame.empty else frame


def evaluations_frame(questionnaire) -> pd.DataFrame:
    criteria = list(EvaluationCriterion.objects.filter(questionnaire=questionnaire))
    records = []
    for ev in (
        Evaluation.objects.filter(response__questionnaire=questionnaire)
        .select_related("response", "evaluator")
        .prefetch_related("scores__criterion")
    ):
        rec: dict[str, Any] = {
            "response_id": ev.response.reference,
            "status": ev.get_status_display(),
            "final_score_pct": float(ev.final_score) if ev.final_score is not None else None,
            "evaluator": str(ev.evaluator),
            "evaluated_at": ev.evaluated_at.replace(tzinfo=None),
            "updated_at": ev.updated_at.replace(tzinfo=None),
            "version": ev.version,
            "comment": ev.comment,
            "recommendations": ev.recommendations,
        }
        by_c = {s.criterion_id: s.score for s in ev.scores.all()}
        for c in criteria:
            rec[f"{c.name} (max {c.max_score}, w {c.weight})"] = float(by_c[c.pk]) if c.pk in by_c else None
        records.append(rec)
    frame = pd.DataFrame.from_records(records)
    return frame.map(safe_cell) if not frame.empty else frame


def statistics_frame(ds: stats.Dataset) -> pd.DataFrame:
    records = []
    for q in ds.questions:
        s = stats.question_summary(ds, q)
        base = {"question_number": q.number, "question_code": q.code, "question": q.text, "type": q.get_qtype_display(), "N": s["N"], "n": s["n"], "missing": s["missing"]}
        if s.get("rows"):
            for r in s["rows"]:
                records.append({**base, "option": r["option"], "code": r["value"], "count": r["count"], "percent": r["pct"]})
        st = s.get("stats") or {}
        if "mean" in st:
            records.append(
                {
                    **base,
                    "option": _("[descriptive statistics]"),
                    "mean": st.get("mean"),
                    "median": st.get("median"),
                    "mode": ", ".join(str(m) for m in st.get("mode") or []),
                    "min": st.get("min"),
                    "max": st.get("max"),
                    "std": st.get("std"),
                    "variance": st.get("variance"),
                }
            )
        if not s.get("rows") and "mean" not in st:
            records.append(base)
    frame = pd.DataFrame.from_records(records)
    return frame.map(safe_cell) if not frame.empty else frame


def codebook_frame(ds: stats.Dataset) -> pd.DataFrame:
    records = []
    for q in ds.questions:
        options = "; ".join(f"{_option_code(o)} = {o.text}" for o in q.choice_options())
        if q.qtype == Question.Type.MATRIX:
            options = "rows: " + "; ".join(r.text for r in q.matrix_rows()) + " | columns: " + "; ".join(
                f"{_option_code(c)} = {c.text}" for c in q.matrix_columns()
            )
        records.append(
            {
                "section": q.section.title,
                "number": q.number,
                "code": q.code,
                "question": q.text,
                "type": q.get_qtype_display(),
                "required": _("yes") if q.required else _("no"),
                "codes": options or None,
            }
        )
    return pd.DataFrame.from_records(records).map(safe_cell)


def per_response_answers(response: SurveyResponse) -> list[dict[str, Any]]:
    """Ordered list of {section, question, answers[]} for one response."""
    grouped: dict[int, list] = defaultdict(list)
    for a in response.answers.select_related("option", "row"):
        grouped[a.question_id].append(a)
    items = []
    for q in (
        Question.objects.filter(questionnaire_id=response.questionnaire_id)
        .select_related("section")
        .order_by("section__order", "section_id", "order", "id")
    ):
        items.append({"section": q.section, "question": q, "answers": grouped.get(q.pk, [])})
    return items
