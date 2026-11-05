"""Researcher evaluations — stored separately from original participant answers."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from surveys.models import Questionnaire, SurveyResponse


class EvaluationCriterion(models.Model):
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="criteria")
    name = models.CharField(_("criterion"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    max_score = models.DecimalField(_("maximum score"), max_digits=8, decimal_places=2, default=Decimal("10"))
    weight = models.DecimalField(_("weight"), max_digits=6, decimal_places=2, default=Decimal("1"))
    order = models.PositiveIntegerField(default=0)
    auto_rule = models.JSONField(
        _("automatic scoring rule"), default=dict, blank=True,
        help_text=_("Transparent rule used for automatic evaluation; empty means manual scoring only."),
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(max_score__gt=0), name="criterion_max_score_positive"),
            models.CheckConstraint(condition=Q(weight__gt=0), name="criterion_weight_positive"),
            models.UniqueConstraint(fields=["questionnaire", "name"], name="unique_criterion_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Evaluation(models.Model):
    class Status(models.TextChoices):
        IN_REVIEW = "in_review", _("In review")
        REVIEWED = "reviewed", _("Reviewed")

    response = models.OneToOneField(SurveyResponse, on_delete=models.CASCADE, related_name="evaluation")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_REVIEW, db_index=True)
    comment = models.TextField(_("scientific comment"), blank=True)
    recommendations = models.TextField(_("recommendations"), blank=True)
    final_score = models.DecimalField(
        _("final score (%)"), max_digits=6, decimal_places=2, null=True, blank=True,
        help_text=_("Weighted score normalised to 0–100."),
    )
    evaluator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="evaluations")
    evaluated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)
    is_automatic = models.BooleanField(_("automatic evaluation"), default=False, db_index=True)
    auto_details = models.JSONField(default=list, blank=True, help_text="Per-criterion explanation of automatic scores.")

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(final_score__isnull=True) | (Q(final_score__gte=0) & Q(final_score__lte=100)),
                name="evaluation_final_score_range",
            )
        ]

    def __str__(self) -> str:
        return f"Evaluation of {self.response.reference}"


class CriterionScore(models.Model):
    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.PROTECT, related_name="scores")
    score = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["evaluation", "criterion"], name="unique_score_per_criterion"),
            models.CheckConstraint(condition=Q(score__gte=0), name="score_non_negative"),
        ]


class EvaluationHistory(models.Model):
    """Snapshot of every saved evaluation version (append-only)."""

    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="history")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["evaluation", "version"], name="unique_evaluation_version")]
