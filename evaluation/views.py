from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from core.audit import log_action
from core.forms import StyledFormMixin
from core.permissions import staff_required
from reports.datasets import per_response_answers
from surveys.models import Questionnaire, SurveyResponse
from surveys.views import get_questionnaire, get_response

from . import services
from .models import Evaluation, EvaluationCriterion


class CriterionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EvaluationCriterion
        fields = ["name", "description", "max_score", "weight", "order"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        labels = {"order": gettext_lazy("Order")}


CriterionFormSet = modelformset_factory(EvaluationCriterion, form=CriterionForm, extra=1, can_delete=True)


@staff_required
def queue(request):
    visible = Questionnaire.objects.visible_to(request.user)
    qs = (
        SurveyResponse.objects.filter(questionnaire__in=visible, status=SurveyResponse.Status.COMPLETED)
        .select_related("questionnaire", "participant", "evaluation")
        .order_by("-submitted_at")
    )
    state = request.GET.get("state", "pending")
    if state == "pending":
        qs = qs.exclude(evaluation__status=Evaluation.Status.REVIEWED)
    elif state == "reviewed":
        qs = qs.filter(evaluation__status=Evaluation.Status.REVIEWED)
    qid = request.GET.get("questionnaire", "")
    if qid.isdigit():
        qs = qs.filter(questionnaire_id=int(qid))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "evaluation/queue.html", {"page": page, "state": state, "qid": qid, "questionnaires": visible.order_by("title")})


@staff_required
def evaluate(request, pk: int):
    response = get_response(request.user, pk)
    if not response.is_completed:
        messages.error(request, _("Only completed responses can be evaluated."))
        return redirect(response)
    criteria = list(EvaluationCriterion.objects.filter(questionnaire=response.questionnaire))
    evaluation = Evaluation.objects.filter(response=response).prefetch_related("scores", "history__changed_by").first()
    current_scores = {s.criterion_id: s.score for s in evaluation.scores.all()} if evaluation else {}
    errors: list[str] = []
    if request.method == "POST":
        scores: dict[int, Decimal | None] = {}
        for c in criteria:
            raw = request.POST.get(f"score_{c.pk}", "").replace(",", ".").strip()
            if raw == "":
                scores[c.pk] = None
                continue
            try:
                scores[c.pk] = Decimal(raw)
            except InvalidOperation:
                errors.append(_("“%(c)s”: enter a number.") % {"c": c.name})
        if not errors:
            try:
                ev = services.save_evaluation(
                    response,
                    request.user,
                    scores=scores,
                    comment=request.POST.get("comment", "")[:10000],
                    recommendations=request.POST.get("recommendations", "")[:10000],
                    reviewed=request.POST.get("reviewed") == "on",
                )
            except ValidationError as exc:
                errors.extend(exc.messages)
            else:
                log_action(request, "evaluation.save", ev, version=ev.version, final=str(ev.final_score))
                messages.success(request, _("Evaluation saved (version %(v)s).") % {"v": ev.version})
                if request.POST.get("next") == "queue":
                    return redirect("evaluation:queue")
                return redirect("evaluation:evaluate", pk=response.pk)
        current_scores = {c.pk: request.POST.get(f"score_{c.pk}", "") for c in criteria}
    rows = [{"criterion": c, "value": current_scores.get(c.pk, "")} for c in criteria]
    return render(
        request,
        "evaluation/evaluate.html",
        {
            "response": response,
            "items": per_response_answers(response),
            "evaluation": evaluation,
            "rows": rows,
            "errors": errors,
            "history": evaluation.history.all() if evaluation else [],
        },
    )


@staff_required
def criteria(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    formset = CriterionFormSet(request.POST or None, queryset=EvaluationCriterion.objects.filter(questionnaire=qn), prefix="crit")
    if request.method == "POST" and formset.is_valid():
        try:
            instances = formset.save(commit=False)
            for obj in formset.deleted_objects:
                obj.delete()
            for obj in instances:
                obj.questionnaire = qn
                obj.save()
        except ProtectedError:
            messages.error(request, _("Criteria already used in evaluations cannot be deleted."))
        else:
            log_action(request, "evaluation.criteria", qn)
            messages.success(request, _("Evaluation criteria saved."))
            return redirect("evaluation:criteria", pk=qn.pk)
    sources = (
        Questionnaire.objects.visible_to(request.user)
        .exclude(pk=qn.pk)
        .filter(criteria__isnull=False)
        .distinct()
        .order_by("-updated_at")
    )
    return render(request, "evaluation/criteria.html", {"questionnaire": qn, "formset": formset, "sources": sources})


@staff_required
@require_POST
def copy_criteria(request, pk: int):
    """Copy criteria from another questionnaire the user can manage (existing names are kept)."""
    qn = get_questionnaire(request.user, pk)
    source_id = request.POST.get("source", "")
    if not source_id.isdigit():
        messages.error(request, _("Choose a questionnaire."))
        return redirect("evaluation:criteria", pk=qn.pk)
    source = get_questionnaire(request.user, int(source_id))
    existing = set(EvaluationCriterion.objects.filter(questionnaire=qn).values_list("name", flat=True))
    copies = [
        EvaluationCriterion(
            questionnaire=qn, name=c.name, description=c.description, max_score=c.max_score, weight=c.weight, order=c.order,
            auto_rule=c.auto_rule,
        )
        for c in EvaluationCriterion.objects.filter(questionnaire=source)
        if c.name not in existing
    ]
    EvaluationCriterion.objects.bulk_create(copies)
    log_action(request, "evaluation.criteria_copy", qn, source=source.pk, count=len(copies))
    messages.success(request, _("%(n)s criteria copied.") % {"n": len(copies)})
    return redirect("evaluation:criteria", pk=qn.pk)


@staff_required
@require_POST
def run_auto(request):
    """Score not-yet-evaluated responses (and refresh unreviewed automatic ones) with the rules."""
    visible = Questionnaire.objects.visible_to(request.user)
    qid = request.POST.get("questionnaire", "")
    targets = visible.filter(pk=int(qid)) if qid.isdigit() else visible
    total = 0
    for qn in targets:
        total += services.auto_evaluate_pending(qn, force=True)
    log_action(request, "evaluation.auto_run", None, count=total)
    if total:
        messages.success(request, _("%(n)s responses were evaluated automatically. Review the scores and mark them as reviewed.") % {"n": total})
    else:
        messages.info(request, _("No responses needed automatic evaluation (or no criteria have automatic rules)."))
    url = reverse("evaluation:queue")
    return redirect(f"{url}?questionnaire={qid}" if qid.isdigit() else url)


@staff_required
@require_POST
def mark_reviewed(request, pk: int):
    response = get_response(request.user, pk)
    evaluation = get_object_or_404(Evaluation, response=response)
    services.save_evaluation(
        response,
        request.user,
        scores={s.criterion_id: s.score for s in evaluation.scores.all()},
        comment=evaluation.comment,
        recommendations=evaluation.recommendations,
        reviewed=True,
    )
    messages.success(request, _("Marked as reviewed."))
    return redirect("evaluation:evaluate", pk=response.pk)
