"""Evaluation logic. Never touches ``IndividualAnswer`` rows."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from surveys.models import SurveyResponse

from .models import CriterionScore, Evaluation, EvaluationCriterion, EvaluationHistory


def weighted_score(scores: Mapping[EvaluationCriterion, Decimal]) -> Decimal | None:
    """Weighted percentage: Σ(score/max·weight) / Σweight · 100."""
    if not scores:
        return None
    total_weight = sum((c.weight for c in scores), Decimal("0"))
    if total_weight <= 0:
        return None
    acc = sum(((s / c.max_score) * c.weight for c, s in scores.items()), Decimal("0"))
    return (acc / total_weight * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _snapshot(evaluation: Evaluation) -> dict:
    return {
        "status": evaluation.status,
        "comment": evaluation.comment,
        "recommendations": evaluation.recommendations,
        "final_score": str(evaluation.final_score) if evaluation.final_score is not None else None,
        "automatic": evaluation.is_automatic,
        "scores": {
            s.criterion.name: {"score": str(s.score), "max": str(s.criterion.max_score), "weight": str(s.criterion.weight)}
            for s in evaluation.scores.select_related("criterion")
        },
    }


@transaction.atomic
def save_evaluation(
    response: SurveyResponse,
    user,
    *,
    scores: Mapping[int, Decimal | None],
    comment: str,
    recommendations: str,
    reviewed: bool,
    automatic: bool = False,
    auto_details: list | None = None,
) -> Evaluation:
    if not response.is_completed:
        raise ValidationError(_("Only completed responses can be evaluated."))
    criteria = {c.pk: c for c in EvaluationCriterion.objects.filter(questionnaire_id=response.questionnaire_id)}
    clean: dict[EvaluationCriterion, Decimal] = {}
    for cid, value in scores.items():
        if value is None:
            continue
        criterion = criteria.get(cid)
        if criterion is None:
            raise ValidationError(_("Unknown evaluation criterion."))
        if value < 0 or value > criterion.max_score:
            raise ValidationError(
                _("Score for “%(c)s” must be between 0 and %(m)s.") % {"c": criterion.name, "m": criterion.max_score}
            )
        clean[criterion] = value

    evaluation = Evaluation.objects.select_for_update().filter(response=response).first()
    if evaluation is None:
        evaluation = Evaluation(response=response, evaluator=user, version=0)
    evaluation.version += 1
    evaluation.evaluator = user
    evaluation.comment = comment
    evaluation.recommendations = recommendations
    evaluation.status = Evaluation.Status.REVIEWED if reviewed else Evaluation.Status.IN_REVIEW
    evaluation.final_score = weighted_score(clean) if len(clean) == len(criteria) and criteria else None
    evaluation.is_automatic = automatic
    evaluation.auto_details = (auto_details or []) if automatic else []
    evaluation.save()
    evaluation.scores.all().delete()
    CriterionScore.objects.bulk_create([CriterionScore(evaluation=evaluation, criterion=c, score=s) for c, s in clean.items()])
    EvaluationHistory.objects.create(
        evaluation=evaluation, version=evaluation.version, snapshot=_snapshot(evaluation), changed_by=user
    )
    return evaluation


# --------------------------------------------------------------------------- automatic evaluation
AUTO_COMMENT = "Арзёбии худкор аз рӯи қоидаҳои шаффоф. Муҳаққиқ метавонад холҳоро санҷида, тағйир диҳад."


def auto_evaluate(response: SurveyResponse, *, force: bool = False) -> Evaluation | None:
    """Score a completed response with the criteria's automatic rules.

    Manual evaluations and automatic ones already marked as reviewed are never
    overwritten (unless ``force`` for an unreviewed automatic one).
    Returns None when nothing was done.
    """
    from .autoscore import collect, score_criterion

    if not response.is_completed:
        return None
    criteria = [c for c in EvaluationCriterion.objects.filter(questionnaire_id=response.questionnaire_id) if c.auto_rule]
    if not criteria:
        return None
    existing = Evaluation.objects.filter(response=response).first()
    keep_comment = ""
    if existing is not None:
        if existing.status == Evaluation.Status.REVIEWED:
            return None
        if not existing.is_automatic:
            # A manual evaluation with at least one score is the researcher's work: never replace it.
            # An empty one (saved before criteria existed) is replaced, keeping its text.
            if existing.scores.exists():
                return None
            keep_comment = existing.comment
        elif not force:
            return None
    answers = collect(response)
    scores: dict[int, Decimal | None] = {}
    details = []
    for c in criteria:
        value, notes = score_criterion(c.auto_rule, answers, c.max_score)
        scores[c.pk] = value
        details.append({"criterion": c.name, "score": str(value) if value is not None else None, "max": str(c.max_score), "notes": notes})
    owner = response.questionnaire.owner
    comment = f"{keep_comment}\n\n{AUTO_COMMENT}".strip() if keep_comment else AUTO_COMMENT
    recommendations = existing.recommendations if existing is not None and not existing.is_automatic else ""
    return save_evaluation(
        response, owner, scores=scores, comment=comment, recommendations=recommendations, reviewed=False,
        automatic=True, auto_details=details,
    )


def auto_evaluate_pending(questionnaire=None, *, force: bool = False) -> int:
    qs = SurveyResponse.objects.filter(status=SurveyResponse.Status.COMPLETED).select_related("questionnaire")
    if questionnaire is not None:
        qs = qs.filter(questionnaire=questionnaire)
    if not force:
        # not evaluated yet, or only an empty unreviewed evaluation
        qs = qs.filter(Q(evaluation__isnull=True) | Q(evaluation__scores__isnull=True, evaluation__status=Evaluation.Status.IN_REVIEW)).distinct()
    done = 0
    for r in qs.order_by("pk"):
        if auto_evaluate(r, force=force) is not None:
            done += 1
    return done
