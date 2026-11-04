"""Statistical analysis computed live from stored PostgreSQL responses.

Rules: every result carries its sample size (N / n); significance is only
reported together with the test that produced it; comparisons are refused
when groups are too small.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import multimode
from typing import Any

import numpy as np
import pandas as pd
from django.db.models import Prefetch
from django.utils.translation import gettext as _
from scipy import stats as sps

from surveys.models import AnswerOption, IndividualAnswer, Participant, Question, Questionnaire, SurveyResponse

ALPHA = 0.05
MIN_GROUP_N = 5
PARTICIPANT_VARIABLES = [
    ("gender", Participant._meta.get_field("gender")),
    ("academic_year", Participant._meta.get_field("academic_year")),
    ("faculty", Participant._meta.get_field("faculty")),
    ("specialty", Participant._meta.get_field("specialty")),
    ("university", Participant._meta.get_field("university")),
    ("education_level", Participant._meta.get_field("education_level")),
    ("group_name", Participant._meta.get_field("group_name")),
]
PARTICIPANT_LABELS = {
    "gender": "Gender",
    "academic_year": "Academic year",
    "faculty": "Faculty",
    "specialty": "Specialty",
    "university": "University",
    "education_level": "Education level",
    "group_name": "Group",
}


def _r(x: float | None, digits: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), digits)


def describe(values: list[float]) -> dict[str, Any]:
    """Descriptive statistics for a numeric sample (sample SD/variance, ddof=1)."""
    arr = np.asarray(values, dtype=float)
    n = int(arr.size)
    if n == 0:
        return {"n": 0}
    modes = multimode(arr.tolist())
    return {
        "n": n,
        "mean": _r(arr.mean(), 3),
        "median": _r(float(np.median(arr)), 3),
        "mode": [_r(m, 3) for m in modes] if len(modes) < n or n == 1 else [],
        "min": _r(arr.min(), 3),
        "max": _r(arr.max(), 3),
        "std": _r(arr.std(ddof=1), 3) if n > 1 else None,
        "variance": _r(arr.var(ddof=1), 3) if n > 1 else None,
        "q1": _r(float(np.percentile(arr, 25)), 3),
        "q3": _r(float(np.percentile(arr, 75)), 3),
    }


@dataclass
class Dataset:
    questionnaire: Questionnaire
    questions: list[Question]
    responses: dict[int, SurveyResponse]
    answers: dict[int, list[IndividualAnswer]] = field(default_factory=dict)  # question_id -> answers

    @property
    def n(self) -> int:
        return len(self.responses)

    @property
    def response_ids(self) -> list[int]:
        return list(self.responses)

    def question(self, qid: int) -> Question | None:
        return next((q for q in self.questions if q.pk == qid), None)

    # ---- variables used by cross-tabs and comparisons ---------------------
    def categorical_variables(self) -> list[tuple[str, str]]:
        out = [(f"q:{q.pk}", q.display_label[:120]) for q in self.questions if q.is_categorical]
        for name, _f in PARTICIPANT_VARIABLES:
            if any(getattr(r.participant, name) for r in self.responses.values()):
                out.append((f"p:{name}", _(PARTICIPANT_LABELS[name])))
        return out

    def numeric_variables(self) -> list[tuple[str, str]]:
        out = [(f"q:{q.pk}", q.display_label[:120]) for q in self.questions if q.is_numeric]
        for section in {q.section_id: q.section for q in self.questions if q.qtype == Question.Type.LIKERT}.values():
            out.append((f"s:{section.pk}", _("Section mean: %(t)s") % {"t": section.title[:100]}))
        return out

    def categorical_series(self, var: str) -> tuple[pd.Series, list[str], str]:
        """(series indexed by response id, ordered categories, label)."""
        kind, _sep, ident = var.partition(":")
        if kind == "p":
            if ident not in PARTICIPANT_LABELS:
                raise ValueError("unknown variable")
            data = {rid: (getattr(r.participant, ident) or "").strip() for rid, r in self.responses.items()}
            data = {k: v for k, v in data.items() if v}
            cats = sorted(set(data.values()))
            return pd.Series(data, dtype="object"), cats, _(PARTICIPANT_LABELS[ident])
        q = self.question(int(ident)) if kind == "q" and ident.isdigit() else None
        if q is None or not q.is_categorical:
            raise ValueError("unknown variable")
        cats = [o.text for o in q.choice_options()]
        data = {a.response_id: a.option.text for a in self.answers.get(q.pk, []) if a.option_id}
        return pd.Series(data, dtype="object"), cats, q.display_label

    def numeric_series(self, var: str) -> tuple[pd.Series, str]:
        kind, _sep, ident = var.partition(":")
        if kind == "s" and ident.isdigit():
            items = [q for q in self.questions if q.section_id == int(ident) and q.qtype == Question.Type.LIKERT]
            if not items:
                raise ValueError("unknown variable")
            frame = pd.DataFrame({q.pk: self.numeric_series(f"q:{q.pk}")[0] for q in items})
            # Composite only for respondents who answered at least half of the items
            mask = frame.notna().sum(axis=1) >= max(1, math.ceil(len(items) / 2))
            return frame[mask].mean(axis=1), _("Section mean: %(t)s") % {"t": items[0].section.title}
        q = self.question(int(ident)) if kind == "q" and ident.isdigit() else None
        if q is None or not q.is_numeric:
            raise ValueError("unknown variable")
        data: dict[int, float] = {}
        for a in self.answers.get(q.pk, []):
            if q.qtype == Question.Type.LIKERT and a.option_id and a.option.value is not None:
                data[a.response_id] = float(a.option.value)
            elif a.number_value is not None:
                data[a.response_id] = float(a.number_value)
        return pd.Series(data, dtype="float"), q.display_label


def load_dataset(questionnaire: Questionnaire, *, include_incomplete: bool = False) -> Dataset:
    statuses = [SurveyResponse.Status.COMPLETED]
    if include_incomplete:
        statuses.append(SurveyResponse.Status.IN_PROGRESS)
    responses = {
        r.pk: r
        for r in SurveyResponse.objects.filter(questionnaire=questionnaire, status__in=statuses)
        .select_related("participant")
        .order_by("pk")
    }
    questions = list(
        Question.objects.filter(questionnaire=questionnaire)
        .select_related("section")
        .prefetch_related(Prefetch("options", queryset=AnswerOption.objects.order_by("order", "id")))
        .order_by("section__order", "section_id", "order", "id")
    )
    answers: dict[int, list[IndividualAnswer]] = defaultdict(list)
    for a in IndividualAnswer.objects.filter(response_id__in=list(responses)).select_related("option", "row"):
        answers[a.question_id].append(a)
    return Dataset(questionnaire=questionnaire, questions=questions, responses=responses, answers=dict(answers))


# --------------------------------------------------------------------------- per question
def question_summary(ds: Dataset, q: Question) -> dict[str, Any]:
    answers = ds.answers.get(q.pk, [])
    answered = {a.response_id for a in answers}
    n_answered = len(answered)
    N = ds.n
    summary: dict[str, Any] = {
        "question": q,
        "type": q.qtype,
        "N": N,
        "n": n_answered,
        "missing": N - n_answered,
        "missing_pct": _r(100 * (N - n_answered) / N, 1) if N else None,
        "chart": None,
    }
    if q.qtype in (Question.Type.SINGLE, Question.Type.YES_NO, Question.Type.LIKERT, Question.Type.MULTIPLE):
        counts = Counter(a.option_id for a in answers if a.option_id)
        rows = []
        for o in q.choice_options():
            c = counts.get(o.pk, 0)
            rows.append(
                {
                    "option": o.text,
                    "value": o.value,
                    "count": c,
                    "pct": _r(100 * c / n_answered, 1) if n_answered else 0,
                }
            )
        summary["rows"] = rows
        summary["pct_base"] = _("respondents who answered (multiple answers possible)") if q.qtype == Question.Type.MULTIPLE else _("valid answers")
        others = [(a.response_id, a.text_value) for a in answers if a.text_value]
        summary["other_texts"] = others
        summary["chart"] = {
            "type": "pie" if q.qtype in (Question.Type.YES_NO,) or (q.qtype == Question.Type.SINGLE and len(rows) <= 4) else "bar",
            "horizontal": q.qtype == Question.Type.MULTIPLE or len(rows) > 6,
            "labels": [r["option"] for r in rows],
            "values": [r["count"] for r in rows],
        }
        if q.qtype == Question.Type.LIKERT:
            values = [float(a.option.value) for a in answers if a.option_id and a.option.value is not None]
            summary["stats"] = describe(values)
    elif q.qtype in (Question.Type.RATING, Question.Type.NUMBER):
        values = [float(a.number_value) for a in answers if a.number_value is not None]
        summary["stats"] = describe(values)
        if q.qtype == Question.Type.RATING:
            c = Counter(int(v) for v in values)
            labels = q.rating_values()
            summary["rows"] = [
                {"option": str(v), "value": v, "count": c.get(v, 0), "pct": _r(100 * c.get(v, 0) / n_answered, 1) if n_answered else 0}
                for v in labels
            ]
            summary["chart"] = {"type": "bar", "horizontal": False, "labels": [str(v) for v in labels], "values": [c.get(v, 0) for v in labels]}
        elif values:
            hist, edges = np.histogram(values, bins=min(10, max(1, len(set(values)))))
            summary["chart"] = {
                "type": "histogram",
                "horizontal": False,
                "labels": [f"{_r(edges[i], 1)}–{_r(edges[i + 1], 1)}" for i in range(len(hist))],
                "values": [int(h) for h in hist],
            }
    elif q.qtype == Question.Type.DATE:
        dates = sorted(a.date_value for a in answers if a.date_value)
        summary["stats"] = {"n": len(dates), "min": dates[0].isoformat() if dates else None, "max": dates[-1].isoformat() if dates else None}
        by_month = Counter(d.strftime("%Y-%m") for d in dates)
        summary["chart"] = {"type": "line", "horizontal": False, "labels": sorted(by_month), "values": [by_month[k] for k in sorted(by_month)]}
    elif q.is_text:
        texts = [(a.response_id, a.text_value) for a in answers if a.text_value]
        lengths = [len(t.split()) for _rid, t in texts]
        summary["texts"] = texts
        summary["stats"] = {"n": len(texts), "mean_words": _r(float(np.mean(lengths)), 1) if lengths else None}
    elif q.qtype == Question.Type.MATRIX:
        rows, cols = q.matrix_rows(), q.matrix_columns()
        counts = Counter((a.row_id, a.option_id) for a in answers)
        summary["matrix"] = {
            "columns": [c.text for c in cols],
            "rows": [{"row": r.text, "counts": [counts.get((r.pk, c.pk), 0) for c in cols]} for r in rows],
        }
        summary["chart"] = {
            "type": "stacked",
            "labels": [r.text for r in rows],
            "datasets": [{"label": c.text, "values": [counts.get((r.pk, c.pk), 0) for r in rows]} for c in cols],
        }
    elif q.qtype == Question.Type.RANKING:
        by_opt: dict[int, list[int]] = defaultdict(list)
        for a in answers:
            if a.option_id and a.rank:
                by_opt[a.option_id].append(a.rank)
        rows = []
        for o in q.choice_options():
            ranks = by_opt.get(o.pk, [])
            rows.append({"option": o.text, "n": len(ranks), "mean_rank": _r(float(np.mean(ranks)), 2) if ranks else None, "first": ranks.count(1)})
        rows.sort(key=lambda r: (r["mean_rank"] is None, r["mean_rank"] or 0))
        summary["ranking"] = rows
        summary["chart"] = {"type": "bar", "horizontal": True, "labels": [r["option"] for r in rows], "values": [r["mean_rank"] or 0 for r in rows], "dataset_label": _("Mean rank (lower = more important)")}
    return summary


def overview(ds: Dataset) -> dict[str, Any]:
    qs = ds.questionnaire
    total_started = SurveyResponse.objects.filter(questionnaire=qs).count()
    durations = [r.duration_seconds for r in ds.responses.values() if r.duration_seconds is not None]
    by_day = Counter(r.submitted_at.date().isoformat() for r in ds.responses.values() if r.submitted_at)
    days = sorted(by_day)
    return {
        "N": ds.n,
        "started": total_started,
        "incomplete": total_started - ds.n,
        "completion_rate": _r(100 * ds.n / total_started, 1) if total_started else None,
        "median_minutes": _r(float(np.median(durations)) / 60, 1) if durations else None,
        "timeline": {"labels": days, "values": [by_day[d] for d in days]},
    }


# --------------------------------------------------------------------------- cross-tab
def crosstab(ds: Dataset, row_var: str, col_var: str) -> dict[str, Any]:
    rs, rcats, rlabel = ds.categorical_series(row_var)
    cs, ccats, clabel = ds.categorical_series(col_var)
    joined = pd.concat([rs.rename("r"), cs.rename("c")], axis=1, join="inner").dropna()
    n = int(len(joined))
    result: dict[str, Any] = {"row_label": rlabel, "col_label": clabel, "n": n, "N": ds.n}
    if n == 0:
        result["error"] = _("No respondents answered both questions.")
        return result
    table = pd.crosstab(
        pd.Categorical(joined["r"], categories=rcats), pd.Categorical(joined["c"], categories=ccats), dropna=False
    )
    table = table.loc[(table.sum(axis=1) > 0), (table.sum(axis=0) > 0)]
    result["rows"] = [str(x) for x in table.index]
    result["cols"] = [str(x) for x in table.columns]
    result["counts"] = table.values.tolist()
    result["row_totals"] = table.sum(axis=1).tolist()
    result["col_totals"] = table.sum(axis=0).tolist()
    result["row_pct"] = (table.div(table.sum(axis=1), axis=0) * 100).round(1).values.tolist()
    result["test"] = chi_square(table.values)
    result["chart"] = {
        "type": "grouped",
        "labels": result["rows"],
        "datasets": [{"label": c, "values": table[c_raw].tolist()} for c, c_raw in zip(result["cols"], table.columns)],
    }
    return result


def chi_square(observed: np.ndarray) -> dict[str, Any]:
    observed = np.asarray(observed, dtype=float)
    r, c = observed.shape
    n = observed.sum()
    if r < 2 or c < 2 or n == 0:
        return {"performed": False, "reason": _("A test needs at least a 2×2 table.")}
    chi2, p, dof, expected = sps.chi2_contingency(observed, correction=(r == 2 and c == 2))
    low_expected = float((expected < 5).sum()) / expected.size
    out: dict[str, Any] = {
        "performed": True,
        "name": _("Pearson chi-square test of independence"),
        "statistic": _r(chi2, 3),
        "df": int(dof),
        "p": _r(p, 4),
        "cramers_v": _r(math.sqrt(chi2 / (n * (min(r, c) - 1))), 3) if n and min(r, c) > 1 else None,
        "low_expected_share": _r(100 * low_expected, 1),
        "significant": bool(p < ALPHA),
        "alpha": ALPHA,
        "warning": "",
    }
    if r == 2 and c == 2:
        _odds, p_fisher = sps.fisher_exact(observed)
        out["fisher_p"] = _r(p_fisher, 4)
    if low_expected > 0.2:
        out["warning"] = _(
            "More than 20%% of cells have expected counts below 5; the chi-square approximation may be unreliable."
        ).replace("%%", "%")
    return out


# --------------------------------------------------------------------------- group comparison
def compare_groups(ds: Dataset, value_var: str, group_var: str) -> dict[str, Any]:
    values, vlabel = ds.numeric_series(value_var)
    groups, cats, glabel = ds.categorical_series(group_var)
    joined = pd.concat([values.rename("v"), groups.rename("g")], axis=1, join="inner").dropna()
    result: dict[str, Any] = {"value_label": vlabel, "group_label": glabel, "n": int(len(joined)), "N": ds.n, "groups": []}
    samples = []
    for cat in cats:
        vals = joined.loc[joined["g"] == cat, "v"].astype(float).tolist()
        if not vals:
            continue
        d = describe(vals)
        result["groups"].append({"group": cat, **d})
        samples.append((cat, vals))
    result["chart"] = {
        "type": "bar",
        "labels": [g["group"] for g in result["groups"]],
        "values": [g["mean"] for g in result["groups"]],
        "dataset_label": _("Mean"),
    }
    if len(samples) < 2:
        result["test"] = {"performed": False, "reason": _("At least two groups with data are needed.")}
        return result
    small = [cat for cat, vals in samples if len(vals) < MIN_GROUP_N]
    if small:
        result["test"] = {
            "performed": False,
            "reason": _("Not tested: groups with fewer than %(n)s respondents (%(g)s). Descriptive results only.")
            % {"n": MIN_GROUP_N, "g": ", ".join(small)},
        }
        return result
    arrays = [np.asarray(vals, dtype=float) for _cat, vals in samples]
    if all(np.ptp(a) == 0 for a in arrays) and len({a[0] for a in arrays}) == 1:
        result["test"] = {"performed": False, "reason": _("All values are identical; no test is meaningful.")}
        return result
    if len(arrays) == 2:
        u, p = sps.mannwhitneyu(arrays[0], arrays[1], alternative="two-sided")
        n1, n2 = len(arrays[0]), len(arrays[1])
        result["test"] = {
            "performed": True,
            "name": _("Mann–Whitney U test (two-sided)"),
            "statistic": _r(u, 3),
            "p": _r(p, 4),
            "effect": _r(1 - 2 * u / (n1 * n2), 3),
            "effect_name": _("rank-biserial correlation"),
            "significant": bool(p < ALPHA),
            "alpha": ALPHA,
        }
    else:
        h, p = sps.kruskal(*arrays)
        n = sum(len(a) for a in arrays)
        k = len(arrays)
        result["test"] = {
            "performed": True,
            "name": _("Kruskal–Wallis H test"),
            "statistic": _r(h, 3),
            "df": k - 1,
            "p": _r(p, 4),
            "effect": _r(max(0.0, (h - k + 1) / (n - k)), 3) if n > k else None,
            "effect_name": _("epsilon squared"),
            "significant": bool(p < ALPHA),
            "alpha": ALPHA,
        }
    return result


def correlation(ds: Dataset, var_x: str, var_y: str) -> dict[str, Any]:
    x, xl = ds.numeric_series(var_x)
    y, yl = ds.numeric_series(var_y)
    joined = pd.concat([x.rename("x"), y.rename("y")], axis=1, join="inner").dropna()
    n = int(len(joined))
    out: dict[str, Any] = {"x_label": xl, "y_label": yl, "n": n, "N": ds.n}
    if n < 3 or joined["x"].nunique() < 2 or joined["y"].nunique() < 2:
        out["test"] = {"performed": False, "reason": _("At least 3 paired, non-constant observations are needed.")}
        return out
    rho, p = sps.spearmanr(joined["x"], joined["y"])
    out["test"] = {
        "performed": True,
        "name": _("Spearman rank correlation"),
        "statistic": _r(rho, 3),
        "p": _r(p, 4),
        "significant": bool(p < ALPHA),
        "alpha": ALPHA,
    }
    return out


def section_comparison(ds: Dataset) -> dict[str, Any]:
    rows = []
    for var, label in ds.numeric_variables():
        if not var.startswith("s:"):
            continue
        series, _l = ds.numeric_series(var)
        rows.append({"section": label, **describe(series.dropna().tolist())})
    return {
        "rows": rows,
        "chart": {"type": "bar", "labels": [r["section"] for r in rows], "values": [r.get("mean") for r in rows], "dataset_label": _("Mean")},
    }


# --------------------------------------------------------------------------- quality & missing
def missing_data(ds: Dataset) -> dict[str, Any]:
    per_question = []
    for q in ds.questions:
        answered = len({a.response_id for a in ds.answers.get(q.pk, [])})
        missing = ds.n - answered
        per_question.append(
            {"question": q, "answered": answered, "missing": missing, "pct": _r(100 * missing / ds.n, 1) if ds.n else None}
        )
    answered_by_resp: Counter = Counter()
    for q in ds.questions:
        for rid in {a.response_id for a in ds.answers.get(q.pk, [])}:
            answered_by_resp[rid] += 1
    total_q = len(ds.questions) or 1
    completeness = [100 * answered_by_resp.get(rid, 0) / total_q for rid in ds.responses]
    return {
        "per_question": per_question,
        "mean_completeness": _r(float(np.mean(completeness)), 1) if completeness else None,
        "complete_cases": sum(1 for c in completeness if c >= 100),
    }


def data_quality(ds: Dataset) -> dict[str, Any]:
    durations = {rid: r.duration_seconds for rid, r in ds.responses.items() if r.duration_seconds is not None}
    med = float(np.median(list(durations.values()))) if durations else None
    speeders = sorted(rid for rid, d in durations.items() if med and d < med / 3)
    # Straight-lining: identical answers on all (>=5) Likert items of a section
    straight = []
    sections: dict[int, list[Question]] = defaultdict(list)
    for q in ds.questions:
        if q.qtype == Question.Type.LIKERT:
            sections[q.section_id].append(q)
    for items in sections.values():
        if len(items) < 5:
            continue
        per_resp: dict[int, list[int]] = defaultdict(list)
        for q in items:
            for a in ds.answers.get(q.pk, []):
                if a.option_id:
                    per_resp[a.response_id].append(a.option.value)
        for rid, vals in per_resp.items():
            if len(vals) == len(items) and len(set(vals)) == 1:
                straight.append({"response_id": rid, "section": items[0].section.title, "value": vals[0]})
    ip_counts = Counter(r.participant.ip_hash for r in ds.responses.values() if r.participant.ip_hash)
    shared = sorted(rid for rid, r in ds.responses.items() if r.participant.ip_hash and ip_counts[r.participant.ip_hash] > 1)
    return {
        "median_seconds": _r(med, 0) if med else None,
        "speeders": speeders,
        "straight_liners": straight,
        "shared_network": shared,
    }
