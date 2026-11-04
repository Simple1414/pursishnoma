from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from surveys.models import Questionnaire


class ResearchReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        FINAL = "final", _("Final")

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="reports")
    title = models.CharField(_("report title"), max_length=500)
    objectives = models.TextField(_("research objectives"), blank=True)
    methodology = models.TextField(_("methodology"), blank=True)
    participants_text = models.TextField(_("participant information"), blank=True)
    findings = models.TextField(_("main research findings"), blank=True)
    discussion = models.TextField(_("discussion"), blank=True)
    conclusions = models.TextField(_("conclusions"), blank=True)
    recommendations = models.TextField(_("recommendations"), blank=True)
    include_charts = models.BooleanField(_("include charts"), default=True)
    include_question_analysis = models.BooleanField(_("include analysis of individual questions"), default=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title


class ExportHistory(models.Model):
    class Format(models.TextChoices):
        XLSX = "xlsx", "Excel (XLSX)"
        DOCX = "docx", "Word (DOCX)"
        PDF = "pdf", "PDF"
        CSV = "csv", "CSV"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="exports")
    questionnaire = models.ForeignKey(
        Questionnaire, null=True, blank=True, on_delete=models.SET_NULL, related_name="exports"
    )
    export_type = models.CharField(max_length=40)
    file_format = models.CharField(max_length=8, choices=Format.choices)
    filename = models.CharField(max_length=255)
    record_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "export history"
