from __future__ import annotations

import logging

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from analytics import intelligence
from core.audit import log_action
from core.forms import StyledFormMixin
from core.permissions import staff_required
from surveys.models import Questionnaire
from surveys.views import get_questionnaire, get_response

from . import renderers
from .models import ExportHistory, ResearchReport

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
}


class ReportForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ResearchReport
        fields = [
            "title", "objectives", "methodology", "participants_text", "findings", "discussion",
            "conclusions", "recommendations", "include_charts", "include_question_analysis", "status",
        ]
        widgets = {f: forms.Textarea(attrs={"rows": 5}) for f in ("objectives", "methodology", "participants_text", "findings", "discussion", "conclusions", "recommendations")}


def _file_response(content: bytes, fmt: str, base: str) -> HttpResponse:
    name = f"{slugify(base)[:60] or 'researchsurvey'}-{timezone.localtime():%Y%m%d-%H%M}.{fmt}"
    response = HttpResponse(content, content_type=CONTENT_TYPES[fmt])
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "no-store"
    return response, name


def _record(request, qn, export_type: str, fmt: str, filename: str, count: int) -> None:
    ExportHistory.objects.create(user=request.user, questionnaire=qn, export_type=export_type, file_format=fmt, filename=filename, record_count=count)
    log_action(request, "export", qn, type=export_type, format=fmt, records=count)


def _get_report(user, pk: int) -> ResearchReport:
    report = get_object_or_404(ResearchReport.objects.select_related("questionnaire"), pk=pk)
    if not report.questionnaire.can_be_managed_by(user):
        raise Http404
    return report


# --------------------------------------------------------------------------- reports
@staff_required
def report_list(request):
    visible = Questionnaire.objects.visible_to(request.user)
    reports = ResearchReport.objects.filter(questionnaire__in=visible).select_related("questionnaire", "created_by")
    return render(request, "reports/list.html", {"reports": reports, "questionnaires": visible.order_by("title")})


@staff_required
@require_POST
def report_create(request):
    qid = request.POST.get("questionnaire", "")
    if not qid.isdigit():
        messages.error(request, _("Choose a questionnaire."))
        return redirect("reports:list")
    qn = get_questionnaire(request.user, int(qid))
    report = ResearchReport.objects.create(
        questionnaire=qn,
        title=qn.title,
        objectives=qn.objective,
        methodology=_("Cross-sectional survey using a structured questionnaire administered online. Descriptive statistics (frequencies, percentages, mean, median, mode, standard deviation) were computed for every item; group comparisons used non-parametric tests where group sizes allowed."),
        conclusions=intelligence.draft_conclusions(qn),
        created_by=request.user,
    )
    log_action(request, "report.create", report)
    messages.success(request, _("Report draft created from the questionnaire. Edit every section before exporting."))
    return redirect("reports:edit", pk=report.pk)


@staff_required
def report_edit(request, pk: int):
    report = _get_report(request.user, pk)
    form = ReportForm(request.POST or None, instance=report)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "report.update", report)
        messages.success(request, _("Report saved."))
        return redirect("reports:edit", pk=report.pk)
    return render(request, "reports/edit.html", {"form": form, "report": report})


@staff_required
def report_preview(request, pk: int):
    report = _get_report(request.user, pk)
    blocks = renderers.report_blocks(report)
    import base64

    for b in blocks:
        if b.kind == "image" and b.image:
            b.data_uri = "data:image/png;base64," + base64.b64encode(b.image).decode()
    return render(request, "reports/preview.html", {"report": report, "blocks": blocks})


@staff_required
def report_export(request, pk: int, fmt: str):
    report = _get_report(request.user, pk)
    blocks = renderers.report_blocks(report)
    if fmt == "docx":
        content = renderers.blocks_to_docx(blocks)
    elif fmt == "pdf":
        content = renderers.blocks_to_pdf(blocks, title=report.title)
    else:
        raise Http404
    response, name = _file_response(content, fmt, report.title)
    _record(request, report.questionnaire, "scientific_report", fmt, name, report.questionnaire.responses.filter(status="completed").count())
    return response


@staff_required
@require_POST
def report_delete(request, pk: int):
    report = _get_report(request.user, pk)
    log_action(request, "report.delete", report)
    report.delete()
    messages.success(request, _("Report deleted."))
    return redirect("reports:list")


# --------------------------------------------------------------------------- export center
@staff_required
def export_center(request):
    visible = Questionnaire.objects.visible_to(request.user).with_counts().order_by("-updated_at")
    history = Paginator(ExportHistory.objects.filter(user=request.user).select_related("questionnaire"), 20).get_page(request.GET.get("page"))
    return render(request, "reports/exports.html", {"questionnaires": visible, "history": history})


@staff_required
def export_dataset(request, pk: int, kind: str):
    qn = get_questionnaire(request.user, pk)
    if kind == "xlsx":
        content, n = renderers.build_xlsx(qn)
        fmt = "xlsx"
    elif kind == "csv":
        content, n = renderers.build_csv(qn, labels=True)
        fmt = "csv"
    elif kind == "csv-codes":
        content, n = renderers.build_csv(qn, labels=False)
        fmt = "csv"
    elif kind in ("stats-docx", "stats-pdf"):
        report = ResearchReport(questionnaire=qn, title=f"{qn.title} — {_('Statistical report')}", objectives=qn.objective, include_charts=True)
        blocks = renderers.report_blocks(report)
        fmt = kind.split("-")[1]
        content = renderers.blocks_to_docx(blocks) if fmt == "docx" else renderers.blocks_to_pdf(blocks, title=report.title)
        n = qn.responses.filter(status="completed").count()
    elif kind == "responses-docx":
        from .renderers import Block

        blocks = []
        responses = qn.responses.filter(status="completed").select_related("participant", "questionnaire").order_by("pk")
        for i, r in enumerate(responses):
            if i:
                blocks.append(Block("pagebreak"))
            blocks += renderers.response_blocks(r)
        content = renderers.blocks_to_docx(blocks or [Block("p", _("No data"))])
        fmt, n = "docx", len(responses)
    else:
        raise Http404
    response, name = _file_response(content, fmt, qn.title)
    _record(request, qn, kind, fmt, name, n)
    return response


@staff_required
def export_response(request, pk: int, fmt: str):
    response_obj = get_response(request.user, pk)
    blocks = renderers.response_blocks(response_obj)
    if fmt == "docx":
        content = renderers.blocks_to_docx(blocks)
    elif fmt == "pdf":
        content = renderers.blocks_to_pdf(blocks, title=f"{response_obj.reference} — {response_obj.questionnaire.title}")
    elif fmt == "xlsx":
        content = renderers.build_responses_xlsx([response_obj])
    else:
        raise Http404
    response, name = _file_response(content, fmt, f"{response_obj.reference}")
    _record(request, response_obj.questionnaire, "individual_response", fmt, name, 1)
    return response


@staff_required
def export_selected(request):
    try:
        ids = [int(x) for x in request.GET.getlist("ids")][:500]
    except ValueError as exc:
        raise Http404 from exc
    responses = [get_response(request.user, i) for i in ids]
    if not responses:
        messages.error(request, _("Select at least one response."))
        return redirect("surveys:responses")
    content = renderers.build_responses_xlsx(responses)
    response, name = _file_response(content, "xlsx", "responses")
    _record(request, responses[0].questionnaire, "selected_responses", "xlsx", name, len(responses))
    return response
