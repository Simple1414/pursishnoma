"""Transparent, rule-based automatic evaluation of a response.

A criterion's ``auto_rule`` is a JSON object ``{"components": [...]}``. Each
component looks at specific questions (by variable code, e.g. ``Q22``) and
returns ``(points, max_points, note)``; components whose questions were not
answered return ``None`` and are left out, so a score is never invented for
missing data. The criterion score is ``Σpoints / Σmax × criterion.max_score``.

Option values are the numeric codes of the answer options (1, 2, 3 …).

Component types
---------------
completeness     share of applicable questions answered
                 {"type": "completeness", "points": 10,
                  "not_applicable_if": {"code": "Q15", "values": [11], "codes": ["Q16", ...]}}
option_map       points for the chosen option of a single-choice question
                 {"type": "option_map", "code": "Q13", "map": {"1": 3, "2": 2}, "points": 3}
count_selected   number of options selected in a multiple-choice question
                 {"type": "count_selected", "code": "Q15", "saturate": 4, "points": 2, "exclude_values": [11]}
likert_mean      mean of Likert items rescaled from 1–5 to 0–points
                 {"type": "likert_mean", "codes": ["Q38", ...], "points": 3, "invert": false}
text_depth       length-based depth of open answers (word bands)
                 {"type": "text_depth", "codes": ["Q49", ...], "points": 10,
                  "bands": [[30, 1.0], [15, 0.75], [6, 0.5], [1, 0.2]], "weights": {"Q49": 1}}
consistency      starts at full points, subtracts a penalty per failed check
                 {"type": "consistency", "points": 10, "penalty": 2.5, "checks": [
                    {"type": "reverse_pair", "item": "Q31", "against": ["Q26", ...]},
                    {"type": "conflict", "if": {"code": "Q16", "values": [1, 2]},
                                         "and": {"code": "Q13", "values": [4]}, "note": "…"},
                    {"type": "exclusive_option", "code": "Q15", "value": 11}]}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean
from typing import Any

from surveys.models import Question, SurveyResponse


@dataclass
class Answers:
    values: dict[str, list[int]] = field(default_factory=dict)  # option codes per question code
    texts: dict[str, str] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)
    all_codes: list[str] = field(default_factory=list)

    def one(self, code: str) -> int | None:
        v = self.values.get(code) or []
        return v[0] if v else None


def collect(response: SurveyResponse) -> Answers:
    a = Answers()
    a.all_codes = list(
        Question.objects.filter(questionnaire_id=response.questionnaire_id)
        .order_by("section__order", "order", "id")
        .values_list("code", flat=True)
    )
    for ans in response.answers.select_related("question", "option"):
        code = ans.question.code
        a.answered.add(code)
        if ans.option_id and ans.option.value is not None and not ans.row_id:
            a.values.setdefault(code, []).append(ans.option.value)
        if ans.text_value and not ans.option_id:
            a.texts[code] = ans.text_value
        elif ans.number_value is not None:
            a.values.setdefault(code, []).append(int(ans.number_value))
    return a


def _words(text: str) -> int:
    return len([w for w in text.split() if any(ch.isalnum() for ch in w)])


# --------------------------------------------------------------------------- components
def _completeness(c: dict, a: Answers):
    codes = list(a.all_codes)
    na = c.get("not_applicable_if")
    excluded: list[str] = []
    if na and set(a.values.get(na["code"], [])) & set(na.get("values", [])):
        excluded = [x for x in na.get("codes", []) if x in codes]
        codes = [x for x in codes if x not in excluded]
    if not codes:
        return None
    done = sum(1 for x in codes if x in a.answered)
    note = f"Ҷавоб дода шудааст ба {done} аз {len(codes)} саволи дахлдор"
    if excluded:
        note += f" ({len(excluded)} савол мувофиқи шарти гузариш ба ҳисоб гирифта нашуд)"
    return done / len(codes) * c["points"], c["points"], note


def _option_map(c: dict, a: Answers):
    v = a.one(c["code"])
    if v is None:
        return None
    mapping = {int(k): float(p) for k, p in c["map"].items()}
    pts = mapping.get(v, 0.0)
    return pts, c.get("points", max(mapping.values())), f"{c['code']}: варианти {v} → {pts:g}"


def _count_selected(c: dict, a: Answers):
    if c["code"] not in a.answered:
        return None
    chosen = [v for v in a.values.get(c["code"], []) if v not in set(c.get("exclude_values", []))]
    n = min(len(chosen), c.get("saturate", 4))
    pts = n / c.get("saturate", 4) * c["points"]
    return pts, c["points"], f"{c['code']}: {len(chosen)} вариант интихоб шудааст"


def _likert_mean(c: dict, a: Answers):
    vals = [a.one(code) for code in c["codes"]]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    m = mean(vals)
    frac = (m - 1) / 4
    if c.get("invert"):
        frac = 1 - frac
    return frac * c["points"], c["points"], f"{c['codes'][0]}–{c['codes'][-1]}: миёна {m:.2f} (n = {len(vals)})"


def _text_depth(c: dict, a: Answers):
    bands = sorted(c.get("bands", [[30, 1.0], [15, 0.75], [6, 0.5], [1, 0.2]]), reverse=True)
    weights = c.get("weights", {})
    total_w, acc, parts = 0.0, 0.0, []
    for code in c["codes"]:
        if code not in a.all_codes:
            continue
        w = float(weights.get(code, 1))
        n = _words(a.texts.get(code, ""))
        frac = next((f for limit, f in bands if n >= limit), 0.0)
        total_w += w
        acc += w * frac
        parts.append(f"{code}: {n} калима")
    if not total_w:
        return None
    return acc / total_w * c["points"], c["points"], "; ".join(parts)


def _consistency(c: dict, a: Answers):
    problems: list[str] = []
    evaluated = 0
    for chk in c.get("checks", []):
        t = chk["type"]
        if t == "reverse_pair":
            item = a.one(chk["item"])
            others = [a.one(x) for x in chk["against"]]
            others = [v for v in others if v is not None]
            if item is None or not others:
                continue
            evaluated += 1
            m = mean(others)
            if (item >= 4 and m >= 4) or (item <= 2 and m <= 2):
                problems.append(f"изҳороти баръакс {chk['item']} = {item} бо миёнаи {chk['against'][0]}–{chk['against'][-1]} = {m:.1f} мухолиф аст")
        elif t == "conflict":
            first, second = chk["if"], chk["and"]
            if first["code"] not in a.answered or second["code"] not in a.answered:
                continue
            evaluated += 1
            if set(a.values.get(first["code"], [])) & set(first["values"]) and set(a.values.get(second["code"], [])) & set(second["values"]):
                problems.append(chk.get("note") or f"{first['code']} бо {second['code']} мухолиф аст")
        elif t == "exclusive_option":
            vals = a.values.get(chk["code"], [])
            if not vals:
                continue
            evaluated += 1
            if chk["value"] in vals and len(vals) > 1:
                problems.append(chk.get("note") or f"{chk['code']}: варианти «{chk['value']}» бо вариантҳои дигар якҷоя интихоб шудааст")
    if not evaluated:
        return None
    pts = max(0.0, c["points"] - c.get("penalty", 2.5) * len(problems))
    note = f"{evaluated} санҷиш; " + ("номувофиқатӣ нест" if not problems else "номувофиқатӣ: " + "; ".join(problems))
    return pts, c["points"], note


COMPONENTS = {
    "completeness": _completeness,
    "option_map": _option_map,
    "count_selected": _count_selected,
    "likert_mean": _likert_mean,
    "text_depth": _text_depth,
    "consistency": _consistency,
}


def score_criterion(rule: dict, answers: Answers, max_score: Decimal) -> tuple[Decimal | None, list[str]]:
    got = 0.0
    possible = 0.0
    notes: list[str] = []
    for comp in rule.get("components", []):
        fn = COMPONENTS.get(comp.get("type"))
        if fn is None:
            notes.append(f"номаълум: {comp.get('type')}")
            continue
        result = fn(comp, answers)
        if result is None:
            continue
        pts, mx, note = result
        got += pts
        possible += mx
        notes.append(note)
    if not possible:
        return None, notes or ["маълумоти кофӣ нест"]
    value = Decimal(str(got / possible)) * max_score
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), notes


def validate_rule(rule: Any) -> bool:
    return isinstance(rule, dict) and isinstance(rule.get("components"), list) and all(
        isinstance(c, dict) and c.get("type") in COMPONENTS for c in rule["components"]
    )
