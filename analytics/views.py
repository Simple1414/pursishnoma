from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.audit import log_action
from core.permissions import staff_required
from surveys.models import Questionnaire
from surveys.views import get_questionnaire

from . import ai, intelligence, stats, text
from .models import AIAnalysis, ResearchFinding, ReviewStatus


def _pick_questionnaire(request, view_name: str):
    visible = Questionnaire.objects.visible_to(request.user).order_by("-updated_at")
    return render(request, "analytics/pick.html", {"questionnaires": visible, "view_name": view_name})


def _charts_payload(summaries) -> list[dict]:
    payload = []
    for s in summaries:
        if s.get("chart"):
            payload.append({"id": f"chart-{s['question'].pk}", "title": s["question"].display_label[:120], **s["chart"]})
    return payload


@staff_required
def overview(request, pk: int | None = None):
    if pk is None:
        return _pick_questionnaire(request, "analytics:overview")
    qn = get_questionnaire(request.user, pk)
    ds = stats.load_dataset(qn)
    summaries = [stats.question_summary(ds, q) for q in ds.questions]
    for s in summaries:
        s["chart_id"] = f"chart-{s['question'].pk}"
    ov = stats.overview(ds)
    charts = _charts_payload(summaries)
    charts.append({"id": "chart-timeline", "title": _("Completed responses per day"), "type": "line", "labels": ov["timeline"]["labels"], "values": ov["timeline"]["values"]})
    return render(request, "analytics/overview.html", {"questionnaire": qn, "summaries": summaries, "overview": ov, "charts": charts})


@staff_required
def crosstab(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    ds = stats.load_dataset(qn)
    variables = ds.categorical_variables()
    result = None
    row, col = request.GET.get("row", ""), request.GET.get("col", "")
    valid = {v for v, _l in variables}
    if row and col:
        if row not in valid or col not in valid or row == col:
            messages.error(request, _("Choose two different variables."))
        else:
            result = stats.crosstab(ds, row, col)
    charts = [{"id": "chart-crosstab", "title": "", **result["chart"]}] if result and result.get("chart") else []
    return render(request, "analytics/crosstab.html", {"questionnaire": qn, "variables": variables, "row": row, "col": col, "result": result, "charts": charts, "N": ds.n})


@staff_required
def compare(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    ds = stats.load_dataset(qn)
    numeric = ds.numeric_variables()
    groups = ds.categorical_variables()
    value, group = request.GET.get("value", ""), request.GET.get("group", "")
    result = corr = None
    if value and group:
        if value not in {v for v, _l in numeric} or group not in {v for v, _l in groups}:
            raise Http404
        result = stats.compare_groups(ds, value, group)
    x, y = request.GET.get("x", ""), request.GET.get("y", "")
    if x and y:
        valid = {v for v, _l in numeric}
        if x in valid and y in valid and x != y:
            corr = stats.correlation(ds, x, y)
    sections = stats.section_comparison(ds)
    charts = []
    if result:
        charts.append({"id": "chart-groups", "title": result["value_label"][:100], **result["chart"]})
    if sections["rows"]:
        charts.append({"id": "chart-sections", "title": _("Section means"), "horizontal": True, **sections["chart"]})
    return render(
        request,
        "analytics/compare.html",
        {"questionnaire": qn, "numeric": numeric, "groups": groups, "value": value, "group": group, "result": result, "sections": sections, "x": x, "y": y, "corr": corr, "charts": charts, "N": ds.n},
    )


@staff_required
def intelligence_center(request, pk: int | None = None):
    if pk is None:
        return _pick_questionnaire(request, "analytics:intelligence")
    qn = get_questionnaire(request.user, pk)
    ds = stats.load_dataset(qn)
    quality = stats.data_quality(ds)
    missing = stats.missing_data(ds)
    open_questions = []
    for q in ds.questions:
        if q.is_text:
            docs = [(a.response_id, a.text_value) for a in ds.answers.get(q.pk, []) if a.text_value]
            open_questions.append({"question": q, "analysis": text.analyse(docs) if docs else None})
    other_texts = []
    for q in ds.questions:
        docs = [(a.response_id, a.text_value) for a in ds.answers.get(q.pk, []) if a.option_id and a.text_value]
        if docs:
            other_texts.append({"question": q, "docs": docs})
    findings = ResearchFinding.objects.filter(questionnaire=qn).select_related("reviewed_by")
    ov = stats.overview(ds)
    charts = [{"id": "chart-trend", "title": _("Completed responses per day"), "type": "line", "labels": ov["timeline"]["labels"], "values": ov["timeline"]["values"]}]
    return render(
        request,
        "analytics/intelligence.html",
        {
            "questionnaire": qn,
            "overview": ov,
            "quality": quality,
            "missing": missing,
            "open_questions": open_questions,
            "other_texts": other_texts,
            "findings": findings,
            "conclusions": intelligence.draft_conclusions(qn),
            "charts": charts,
        },
    )


@staff_required
@require_POST
def generate_findings(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    n = intelligence.regenerate_findings(qn, request.user)
    log_action(request, "findings.generate", qn, count=n)
    if n:
        messages.success(request, _("%(n)s draft findings generated from the data. Review and approve them.") % {"n": n})
    else:
        messages.info(request, _("Not enough data for findings yet (at least 5 completed responses per question)."))
    return redirect("analytics:intelligence", pk=qn.pk)


@staff_required
@require_POST
def finding_review(request, pk: int, finding_id: int):
    qn = get_questionnaire(request.user, pk)
    finding = get_object_or_404(ResearchFinding, pk=finding_id, questionnaire=qn)
    action = request.POST.get("action")
    if action == "save":
        finding.text = request.POST.get("text", finding.text)[:5000]
    elif action == "approve":
        finding.text = request.POST.get("text", finding.text)[:5000]
        finding.status = ReviewStatus.APPROVED
    elif action == "reject":
        finding.status = ReviewStatus.REJECTED
    elif action == "delete":
        finding.delete()
        return redirect("analytics:intelligence", pk=qn.pk)
    else:
        raise Http404
    finding.reviewed_by = request.user
    finding.reviewed_at = timezone.now()
    finding.save()
    log_action(request, f"finding.{action}", finding)
    return redirect(f"{_intel_url(qn)}#finding-{finding.pk}")


def _intel_url(qn) -> str:
    from django.urls import reverse

    return reverse("analytics:intelligence", args=[qn.pk])


@staff_required
@require_POST
def finding_add(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    text_value = request.POST.get("text", "").strip()
    if text_value:
        ResearchFinding.objects.create(
            questionnaire=qn,
            source=ResearchFinding.Source.RESEARCHER,
            title=request.POST.get("title", "").strip()[:255] or _("Researcher note"),
            text=text_value[:5000],
            status=ReviewStatus.APPROVED,
            created_by=request.user,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        messages.success(request, _("Finding added."))
    return redirect("analytics:intelligence", pk=qn.pk)


@staff_required
def assistant(request, pk: int | None = None):
    if pk is None:
        return _pick_questionnaire(request, "analytics:assistant")
    qn = get_questionnaire(request.user, pk)
    if request.method == "POST":
        task = request.POST.get("task", "")
        if task not in AIAnalysis.Task.values:
            raise Http404
        language = request.POST.get("language") or get_language() or "tg"
        analysis = ai.run_analysis(qn, task, request.user, request.POST.get("prompt", ""), language=language)
        log_action(request, "ai.run", analysis, task=task, ok=not analysis.error)
        if analysis.error:
            messages.error(request, analysis.error)
        else:
            messages.success(request, _("AI draft created. It is NOT a research result until you review and approve it."))
        return redirect("analytics:assistant", pk=qn.pk)
    analyses = AIAnalysis.objects.filter(questionnaire=qn).select_related("created_by", "reviewed_by")[:30]
    return render(
        request,
        "analytics/assistant.html",
        {"questionnaire": qn, "analyses": analyses, "tasks": AIAnalysis.Task.choices, "configured": ai.is_configured()},
    )


@staff_required
@require_POST
def assistant_review(request, pk: int, analysis_id: int):
    qn = get_questionnaire(request.user, pk)
    analysis = get_object_or_404(AIAnalysis, pk=analysis_id, questionnaire=qn)
    action = request.POST.get("action")
    if action not in {"approve", "reject", "save"}:
        raise Http404
    edited = request.POST.get("edited_text")
    if action == "save":
        analysis.edited_text = (edited or "")[:20000]
        analysis.save(update_fields=["edited_text"])
    else:
        ai.review(analysis, request.user, approve=action == "approve", edited_text=(edited or "")[:20000] if edited is not None else None)
    log_action(request, f"ai.{action}", analysis)
    return redirect("analytics:assistant", pk=qn.pk)
