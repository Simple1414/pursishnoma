"""Stored interpretations. Statistics themselves are always computed live from responses."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from surveys.models import Questionnaire


class ReviewStatus(models.TextChoices):
    DRAFT = "draft", _("Draft — not reviewed")
    APPROVED = "approved", _("Approved by researcher")
    REJECTED = "rejected", _("Rejected")


class ResearchFinding(models.Model):
    """A finding with traceable evidence (question IDs, response IDs, statistics)."""

    class Source(models.TextChoices):
        STATISTICAL = "statistical", _("Statistical result")
        AI = "ai", _("AI-generated interpretation")
        RESEARCHER = "researcher", _("Researcher")

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="findings")
    source = models.CharField(max_length=16, choices=Source.choices)
    title = models.CharField(_("title"), max_length=255)
    text = models.TextField(_("text"))
    evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class AIAnalysis(models.Model):
    class Task(models.TextChoices):
        EXPLAIN = "explain", _("Explain statistical results")
        SUMMARIZE = "summarize", _("Summarize responses")
        PATTERNS = "patterns", _("Identify possible patterns")
        METHODS = "methods", _("Suggest analytical approaches")
        CONCLUSIONS = "conclusions", _("Draft research conclusions")
        DISCUSSION = "discussion", _("Suggest discussion points")

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="ai_analyses")
    task = models.CharField(max_length=20, choices=Task.choices)
    researcher_prompt = models.TextField(blank=True)
    context_data = models.JSONField(default=dict, help_text="Exact data sent to the AI provider.")
    output_text = models.TextField(blank=True)
    edited_text = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True)
    provider = models.CharField(max_length=32, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_task_display()} ({self.created_at:%Y-%m-%d})"

    @property
    def final_text(self) -> str:
        return self.edited_text or self.output_text
