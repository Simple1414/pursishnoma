"""Research Intelligence Center: rule-based, traceable findings from real data.

Nothing here is invented: each finding is a sentence generated from computed
statistics and carries the evidence (question, N, n, counts, response IDs).
Findings start as drafts and become part of reports only after the researcher
approves them.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils.translation import gettext as _

from surveys.models import Question

from . import stats, text
from .models import ResearchFinding, ReviewStatus

MIN_N_FOR_FINDING = 5


def statistical_findings(ds: stats.Dataset) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if ds.n < MIN_N_FOR_FINDING:
        return findings
    likert_means = []
    for q in ds.questions:
        s = stats.question_summary(ds, q)
        if s["n"] < MIN_N_FOR_FINDING:
            continue
        if q.qtype in (Question.Type.SINGLE, Question.Type.YES_NO) and s.get("rows"):
            top = max(s["rows"], key=lambda r: r["count"])
            if top["pct"] and top["pct"] >= 40:
                findings.append(
                    {
                        "title": _("Most frequent answer — question %(n)s") % {"n": q.number or q.code},
                        "text": _("For “%(q)s”, the most frequent answer was “%(o)s”: %(c)s of %(n)s respondents (%(p)s%%).")
                        % {"q": q.text[:200], "o": top["option"], "c": top["count"], "n": s["n"], "p": top["pct"]},
                        "evidence": {"question_id": q.pk, "N": s["N"], "n": s["n"], "rows": s["rows"]},
                    }
                )
        elif q.qtype == Question.Type.MULTIPLE and s.get("rows"):
            ranked = sorted(s["rows"], key=lambda r: -r["count"])[:3]
            parts = "; ".join(f"“{r['option']}” — {r['count']} ({r['pct']}%)" for r in ranked if r["count"])
            if parts:
                findings.append(
                    {
                        "title": _("Most selected options — question %(n)s") % {"n": q.number or q.code},
                        "text": _("For “%(q)s” (multiple answers, n = %(n)s), the most selected options were: %(p)s.")
                        % {"q": q.text[:200], "n": s["n"], "p": parts},
                        "evidence": {"question_id": q.pk, "N": s["N"], "n": s["n"], "rows": s["rows"]},
                    }
                )
        elif q.qtype == Question.Type.LIKERT and s.get("stats", {}).get("mean") is not None:
            values = [r["value"] for r in s["rows"] if r["value"] is not None]
            top_two = sorted(values)[-2:] if len(values) >= 4 else []
            agree = sum(r["count"] for r in s["rows"] if r["value"] in top_two)
            likert_means.append((s["stats"]["mean"], q, s, agree))
        if s["missing_pct"] and s["missing_pct"] >= 20:
            findings.append(
                {
                    "title": _("High missing rate — question %(n)s") % {"n": q.number or q.code},
                    "text": _("Question “%(q)s” was not answered by %(m)s of %(N)s respondents (%(p)s%%); interpret its results with caution.")
                    % {"q": q.text[:200], "m": s["missing"], "N": s["N"], "p": s["missing_pct"]},
                    "evidence": {"question_id": q.pk, "N": s["N"], "missing": s["missing"]},
                }
            )
    if len(likert_means) >= 3:
        likert_means.sort(key=lambda x: x[0])
        for label, (mean, q, s, agree) in (
            (_("Highest-rated statement"), likert_means[-1]),
            (_("Lowest-rated statement"), likert_means[0]),
        ):
            findings.append(
                {
                    "title": f"{label} — {q.number or q.code}",
                    "text": _("“%(q)s”: mean %(m)s (SD %(sd)s, n = %(n)s); %(a)s respondents (%(p)s%%) chose one of the two highest scale points.")
                    % {
                        "q": q.text[:200],
                        "m": mean,
                        "sd": s["stats"].get("std"),
                        "n": s["n"],
                        "a": agree,
                        "p": round(100 * agree / s["n"], 1) if s["n"] else 0,
                    },
                    "evidence": {"question_id": q.pk, "N": s["N"], "n": s["n"], "stats": s["stats"]},
                }
            )
    sections = stats.section_comparison(ds)
    for row in sections["rows"]:
        if row.get("n", 0) >= MIN_N_FOR_FINDING:
            findings.append(
                {
                    "title": row["section"][:255],
                    "text": _("%(s)s: mean %(m)s (SD %(sd)s, median %(md)s, n = %(n)s) on the original response scale.")
                    % {"s": row["section"], "m": row["mean"], "sd": row.get("std"), "md": row["median"], "n": row["n"]},
                    "evidence": {"section_stats": row},
                }
            )
    quality = stats.data_quality(ds)
    if quality["straight_liners"]:
        ids = sorted({x["response_id"] for x in quality["straight_liners"]})
        findings.append(
            {
                "title": _("Possible straight-lining"),
                "text": _("%(k)s responses gave the identical answer to every Likert item of at least one section (≥5 items). Review them before analysis.")
                % {"k": len(ids)},
                "evidence": {"response_ids": ids},
            }
        )
    return findings


def open_ended_findings(ds: stats.Dataset) -> list[dict[str, Any]]:
    out = []
    for q in ds.questions:
        if not q.is_text:
            continue
        docs = [(a.response_id, a.text_value) for a in ds.answers.get(q.pk, []) if a.text_value]
        if len(docs) < MIN_N_FOR_FINDING:
            continue
        res = text.analyse(docs)
        for g in res["groups"][:3]:
            if g["size"] >= 3:
                out.append(
                    {
                        "title": _("Recurring theme — question %(n)s") % {"n": q.number or q.code},
                        "text": _("%(k)s of %(n)s answers to “%(q)s” are similar and share the terms: %(t)s.")
                        % {"k": g["size"], "n": len(docs), "q": q.text[:160], "t": g["label"]},
                        "evidence": {"question_id": q.pk, "response_ids": [i["response_id"] for i in g["items"]]},
                    }
                )
    return out


@transaction.atomic
def regenerate_findings(questionnaire, user) -> int:
    """Replace unreviewed statistical drafts with freshly computed ones."""
    ds = stats.load_dataset(questionnaire)
    ResearchFinding.objects.filter(
        questionnaire=questionnaire, source=ResearchFinding.Source.STATISTICAL, status=ReviewStatus.DRAFT
    ).delete()
    items = statistical_findings(ds) + open_ended_findings(ds)
    ResearchFinding.objects.bulk_create(
        [
            ResearchFinding(
                questionnaire=questionnaire,
                source=ResearchFinding.Source.STATISTICAL,
                title=f["title"][:255],
                text=f["text"],
                evidence=_jsonable(f["evidence"]),
                created_by=user,
            )
            for f in items
        ]
    )
    return len(items)


def draft_conclusions(questionnaire) -> str:
    """Draft conclusions assembled only from researcher-approved findings."""
    approved = ResearchFinding.objects.filter(questionnaire=questionnaire, status=ReviewStatus.APPROVED).order_by("created_at")
    if not approved:
        return ""
    lines = [_("Draft generated from approved findings — review before use.")]
    lines += [f"{i}. {f.text}" for i, f in enumerate(approved, start=1)]
    return "\n".join(lines)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if not isinstance(v, Question)}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)
