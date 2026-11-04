"""Evaluation logic. Never touches ``IndividualAnswer`` rows."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from django.core.exceptions import ValidationError
from django.db import transaction
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
    evaluation.save()
    evaluation.scores.all().delete()
    CriterionScore.objects.bulk_create([CriterionScore(evaluation=evaluation, criterion=c, score=s) for c, s in clean.items()])
    EvaluationHistory.objects.create(
        evaluation=evaluation, version=evaluation.version, snapshot=_snapshot(evaluation), changed_by=user
    )
    return evaluation
