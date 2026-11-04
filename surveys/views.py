"""Researcher-facing views: questionnaires, builder, import, responses."""
from __future__ import annotations

import io
import logging

import qrcode
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, ProtectedError, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.audit import log_action
from core.permissions import staff_required
from evaluation.models import Evaluation

from . import services
from .forms import (
    CustomParticipantFieldForm,
    ImportForm,
    OptionFormSet,
    ParticipantFieldFormSet,
    QuestionForm,
    QuestionnaireForm,
    ResponseFilterForm,
    SectionForm,
)
from .importer import ImportError_, parse_docx
from .models import AnswerOption, Participant, ParticipantField, Question, Questionnaire, Section, SurveyResponse

logger = logging.getLogger(__name__)


def get_questionnaire(user, pk: int) -> Questionnaire:
    """Object-level permission check (IDOR protection)."""
    try:
        return Questionnaire.objects.visible_to(user).get(pk=pk)
    except Questionnaire.DoesNotExist as exc:
        raise Http404 from exc


def get_response(user, pk: int) -> SurveyResponse:
    visible = Questionnaire.objects.visible_to(user)
    try:
        return SurveyResponse.objects.select_related("questionnaire", "participant").get(pk=pk, questionnaire__in=visible)
    except SurveyResponse.DoesNotExist as exc:
        raise Http404 from exc


def public_link(request, questionnaire: Questionnaire) -> str:
    path = questionnaire.get_public_url()
    base = settings.PUBLIC_BASE_URL or request.build_absolute_uri("/").rstrip("/")
    return f"{base}{path}"


# --------------------------------------------------------------------------- list & CRUD
@staff_required
def questionnaire_list(request):
    qs = Questionnaire.objects.visible_to(request.user).with_counts().select_related("owner")
    status = request.GET.get("status", "")
    if status in Questionnaire.Status.values:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(author__icontains=q))
    page = Paginator(qs.order_by("-updated_at"), 20).get_page(request.GET.get("page"))
    return render(request, "surveys/list.html", {"page": page, "status": status, "q": q, "statuses": Questionnaire.Status.choices})


@staff_required
def questionnaire_create(request):
    form = QuestionnaireForm(request.POST or None, initial={"author": str(request.user)})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            qn = form.save(commit=False)
            qn.owner = request.user
            qn.save()
            Section.objects.create(questionnaire=qn, title=_("Section 1"), order=0)
            services.ensure_participant_fields(qn)
        log_action(request, "questionnaire.create", qn)
        messages.success(request, _("Questionnaire created. Add your questions."))
        return redirect(qn)
    return render(request, "surveys/form.html", {"form": form, "title": _("New questionnaire")})


@staff_required
def questionnaire_edit(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    form = QuestionnaireForm(request.POST or None, instance=qn)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "questionnaire.update", qn)
        messages.success(request, _("Changes saved."))
        return redirect(qn)
    return render(request, "surveys/form.html", {"form": form, "title": _("Edit questionnaire"), "questionnaire": qn})


@staff_required
def questionnaire_detail(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    sections = qn.sections.prefetch_related("questions__options")
    counts = SurveyResponse.objects.filter(questionnaire=qn)
    context = {
        "questionnaire": qn,
        "sections": sections,
        "has_responses": counts.exists(),
        "n_completed": counts.filter(status=SurveyResponse.Status.COMPLETED).count(),
        "n_incomplete": counts.filter(status=SurveyResponse.Status.IN_PROGRESS).count(),
        "public_link": public_link(request, qn),
        "section_form": SectionForm(),
        "report": qn.import_report or {},
    }
    return render(request, "surveys/detail.html", context)


@staff_required
@require_POST
def questionnaire_duplicate(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    copy = services.duplicate_questionnaire(qn, request.user)
    log_action(request, "questionnaire.duplicate", copy, source=qn.pk)
    messages.success(request, _("A draft copy was created."))
    return redirect(copy)


@staff_required
def questionnaire_delete(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    if request.method == "POST":
        if qn.responses.exists():
            messages.error(request, _("This questionnaire has responses and cannot be deleted. Close it instead to preserve research data."))
            return redirect(qn)
        title = qn.title
        log_action(request, "questionnaire.delete", qn, title=title)
        qn.delete()
        messages.success(request, _("Questionnaire deleted."))
        return redirect("surveys:list")
    return render(request, "surveys/confirm_delete.html", {"object": qn, "cancel_url": qn.get_absolute_url()})


@staff_required
@require_POST
def questionnaire_status(request, pk: int, action: str):
    qn = get_questionnaire(request.user, pk)
    try:
        if action == "approve":
            services.approve(qn, request.user)
            messages.success(request, _("Questionnaire approved. It can now be published."))
        elif action == "publish":
            services.publish(qn)
            messages.success(request, _("Questionnaire published. Share the public link."))
        elif action == "close":
            qn.status = Questionnaire.Status.CLOSED
            qn.save(update_fields=["status", "updated_at"])
            messages.success(request, _("Questionnaire closed. No new responses are accepted."))
        elif action == "reopen":
            if qn.approved_at is None:
                raise ValueError(_("The questionnaire must be approved before publication."))
            qn.status = Questionnaire.Status.PUBLISHED
            qn.save(update_fields=["status", "updated_at"])
            messages.success(request, _("Questionnaire reopened."))
        elif action == "toggle":
            qn.is_active = not qn.is_active
            qn.save(update_fields=["is_active", "updated_at"])
            messages.success(request, _("Activated.") if qn.is_active else _("Deactivated."))
        elif action == "unapprove":
            if qn.responses.exists():
                raise ValueError(_("Questionnaires with responses cannot return to draft."))
            qn.status = Questionnaire.Status.DRAFT
            qn.approved_at = None
            qn.approved_by = None
            qn.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])
            messages.success(request, _("Returned to draft for editing."))
        else:
            raise Http404
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(qn)
    log_action(request, f"questionnaire.{action}", qn)
    return redirect(qn)


@staff_required
def questionnaire_qr(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    img = qrcode.make(public_link(request, qn), box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    response = HttpResponse(buf.getvalue(), content_type="image/png")
    if request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="questionnaire-{qn.pk}-qr.png"'
    return response


@staff_required
def questionnaire_preview(request, pk: int):
    from .public_views import build_fill_context

    qn = get_questionnaire(request.user, pk)
    context = build_fill_context(qn, values={}, errors={})
    context["preview"] = True
    return render(request, "public/fill.html", context)


@staff_required
def questionnaire_source(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    if not qn.source_file:
        raise Http404
    name = f"{slugify(qn.title)[:60] or 'questionnaire'}.docx"
    return FileResponse(qn.source_file.open("rb"), as_attachment=True, filename=name)


# --------------------------------------------------------------------------- import
@staff_required
def questionnaire_import(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["document"]
        try:
            parsed = parse_docx(upload, fallback_title=upload.name.rsplit(".", 1)[0])
        except ImportError_ as exc:
            form.add_error("document", str(exc))
        except Exception:  # noqa: BLE001 - malformed documents must not crash the page
            logger.exception("DOCX import failed")
            form.add_error("document", _("The document could not be interpreted. Check that it is a valid questionnaire."))
        else:
            if not parsed.questions:
                form.add_error("document", _("No numbered questions were found. Questions must start with a number, e.g. “1. …”."))
            else:
                upload.seek(0)
                qn = services.create_from_parsed(parsed, request.user, form.cleaned_data["language"], source_file=upload)
                log_action(request, "questionnaire.import", qn, questions=len(parsed.questions))
                messages.success(
                    request,
                    _("Imported %(q)s questions in %(s)s sections. Review every question, then approve the questionnaire.")
                    % {"q": len(parsed.questions), "s": len(parsed.sections)},
                )
                return redirect(qn)
    return render(request, "surveys/import.html", {"form": form})


# --------------------------------------------------------------------------- sections
@staff_required
@require_POST
def section_add(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    form = SectionForm(request.POST)
    if form.is_valid():
        section = form.save(commit=False)
        section.questionnaire = qn
        section.order = (qn.sections.aggregate(m=Max("order"))["m"] or 0) + 1
        section.save()
        messages.success(request, _("Section added."))
    else:
        messages.error(request, _("Enter a section title."))
    return redirect(qn)


@staff_required
def section_edit(request, pk: int, section_id: int):
    qn = get_questionnaire(request.user, pk)
    section = get_object_or_404(Section, pk=section_id, questionnaire=qn)
    form = SectionForm(request.POST or None, instance=section)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Section saved."))
        return redirect(qn)
    return render(request, "surveys/section_form.html", {"form": form, "questionnaire": qn, "section": section})


@staff_required
@require_POST
def section_delete(request, pk: int, section_id: int):
    qn = get_questionnaire(request.user, pk)
    section = get_object_or_404(Section, pk=section_id, questionnaire=qn)
    if qn.sections.count() <= 1:
        messages.error(request, _("A questionnaire needs at least one section."))
    else:
        try:
            section.delete()
            services.renumber(qn)
            messages.success(request, _("Section deleted."))
        except ProtectedError:
            messages.error(request, _("This section contains answered questions and cannot be deleted."))
    return redirect(qn)


@staff_required
@require_POST
def section_move(request, pk: int, section_id: int, direction: str):
    qn = get_questionnaire(request.user, pk)
    _swap(list(qn.sections.all()), section_id, direction, Section)
    return redirect(qn)


def _swap(items, target_id: int, direction: str, model) -> None:
    ids = [i.pk for i in items]
    if target_id not in ids:
        raise Http404
    idx = ids.index(target_id)
    other = idx - 1 if direction == "up" else idx + 1
    if 0 <= other < len(items):
        items[idx], items[other] = items[other], items[idx]
        with transaction.atomic():
            for order, obj in enumerate(items):
                model.objects.filter(pk=obj.pk).update(order=order)


# --------------------------------------------------------------------------- questions
@staff_required
def question_edit(request, pk: int, question_id: int | None = None):
    qn = get_questionnaire(request.user, pk)
    question = get_object_or_404(Question, pk=question_id, questionnaire=qn) if question_id else None
    locked = bool(question and question.answers.exists())
    if question is None:
        section_id = request.GET.get("section")
        section = qn.sections.filter(pk=section_id).first() if section_id else qn.sections.last()
        next_number = qn.questions.count() + 1
        initial = {"section": section, "number": str(next_number), "code": services.unique_code(qn, f"Q{next_number}")}
        question = Question(questionnaire=qn)
    else:
        initial = {}
    form = QuestionForm(request.POST or None, instance=question, initial=initial, questionnaire=qn, locked=locked)
    formset = OptionFormSet(request.POST or None, instance=question if question.pk else Question(), prefix="opt")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.questionnaire = qn
                if not obj.pk:
                    obj.order = (Question.objects.filter(section=obj.section).aggregate(m=Max("order"))["m"] or 0) + 1
                obj.save()
                formset.instance = obj
                formset.save()
                if obj.qtype == Question.Type.YES_NO and not obj.options.exists():
                    AnswerOption.objects.bulk_create(
                        [AnswerOption(question=obj, text=_("Yes"), value=1, order=0), AnswerOption(question=obj, text=_("No"), value=0, order=1)]
                    )
                if obj.qtype == Question.Type.LIKERT and not obj.options.exists():
                    AnswerOption.objects.bulk_create([AnswerOption(question=obj, text=str(v), value=v, order=v) for v in range(1, 6)])
                problems = _option_problems(obj)
                if problems:
                    raise ValueError(problems)
        except ProtectedError:
            messages.error(request, _("Options that already have answers cannot be deleted."))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            log_action(request, "question.save", obj)
            messages.success(request, _("Question saved."))
            return redirect(f"{qn.get_absolute_url()}#q{obj.pk}")
    return render(
        request,
        "surveys/question_form.html",
        {"form": form, "formset": formset, "questionnaire": qn, "question": question if question.pk else None, "locked": locked},
    )


def _option_problems(q: Question) -> str:
    opts = list(q.options.all())
    kinds = {o.kind for o in opts}
    if q.qtype in (Question.Type.SINGLE, Question.Type.MULTIPLE, Question.Type.LIKERT, Question.Type.RANKING):
        if len([o for o in opts if o.kind == AnswerOption.Kind.OPTION]) < 2:
            return _("This question type needs at least two answer options.")
    if q.qtype == Question.Type.MATRIX and not ({AnswerOption.Kind.ROW, AnswerOption.Kind.COLUMN} <= kinds):
        return _("Matrix questions need at least one row and one column.")
    if q.qtype == Question.Type.LIKERT and any(o.value is None for o in opts if o.kind == AnswerOption.Kind.OPTION):
        return _("Every Likert option needs a numeric code.")
    return ""


@staff_required
@require_POST
def question_delete(request, pk: int, question_id: int):
    qn = get_questionnaire(request.user, pk)
    question = get_object_or_404(Question, pk=question_id, questionnaire=qn)
    if question.answers.exists():
        messages.error(request, _("This question has answers and cannot be deleted."))
        return redirect(qn)
    question.delete()
    services.renumber(qn)
    log_action(request, "question.delete", qn, question=question_id)
    messages.success(request, _("Question deleted."))
    return redirect(qn)


@staff_required
@require_POST
def question_move(request, pk: int, question_id: int, direction: str):
    qn = get_questionnaire(request.user, pk)
    question = get_object_or_404(Question, pk=question_id, questionnaire=qn)
    _swap(list(question.section.questions.order_by("order", "id")), question.pk, direction, Question)
    return redirect(f"{qn.get_absolute_url()}#q{question.pk}")


# --------------------------------------------------------------------------- participant fields
@staff_required
def participant_fields(request, pk: int):
    qn = get_questionnaire(request.user, pk)
    services.ensure_participant_fields(qn)
    qs = qn.participant_fields.all()
    formset = ParticipantFieldFormSet(request.POST or None, queryset=qs, prefix="pf")
    custom = CustomParticipantFieldForm(request.POST or None, prefix="custom")
    if request.method == "POST":
        if "add_custom" in request.POST:
            if custom.is_valid():
                label = custom.cleaned_data["label"].strip()
                if qn.participant_fields.filter(label=label).exists():
                    messages.error(request, _("A field with this label already exists."))
                else:
                    ParticipantField.objects.create(questionnaire=qn, key=ParticipantField.Key.CUSTOM, label=label, enabled=True, order=qs.count())
                    messages.success(request, _("Custom field added."))
                return redirect("surveys:participant_fields", pk=qn.pk)
        elif formset.is_valid():
            formset.save()
            log_action(request, "questionnaire.participant_fields", qn)
            messages.success(request, _("Participant fields saved."))
            return redirect(qn)
    return render(request, "surveys/participant_fields.html", {"questionnaire": qn, "formset": formset, "custom": custom})


# --------------------------------------------------------------------------- responses
def _filtered_responses(request):
    form = ResponseFilterForm(request.GET or None, user=request.user)
    visible = Questionnaire.objects.visible_to(request.user)
    qs = SurveyResponse.objects.filter(questionnaire__in=visible).select_related("questionnaire", "participant", "evaluation")
    if form.is_valid():
        d = form.cleaned_data
        if d.get("questionnaire"):
            qs = qs.filter(questionnaire=d["questionnaire"])
        if d.get("status"):
            qs = qs.filter(status=d["status"])
        if d.get("evaluation") == "pending":
            qs = qs.filter(status=SurveyResponse.Status.COMPLETED).exclude(evaluation__status=Evaluation.Status.REVIEWED)
        elif d.get("evaluation") == "reviewed":
            qs = qs.filter(evaluation__status=Evaluation.Status.REVIEWED)
        if d.get("date_from"):
            qs = qs.filter(started_at__date__gte=d["date_from"])
        if d.get("date_to"):
            qs = qs.filter(started_at__date__lte=d["date_to"])
        term = (d.get("q") or "").strip()
        if term:
            cond = (
                Q(participant__name__icontains=term)
                | Q(participant__faculty__icontains=term)
                | Q(participant__specialty__icontains=term)
                | Q(participant__group_name__icontains=term)
                | Q(answers__text_value__icontains=term)
            )
            digits = term.upper().removeprefix("R-").lstrip("0")
            if digits.isdigit():
                cond |= Q(pk=int(digits))
            qs = qs.filter(cond).distinct()
        sort = d.get("sort") or "-started_at"
        qs = qs.order_by(sort, "-pk")
    else:
        qs = qs.order_by("-started_at")
    return form, qs


@staff_required
def response_list(request):
    form, qs = _filtered_responses(request)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "surveys/responses.html", {"form": form, "page": page, "querystring": params.urlencode()})


@staff_required
def response_detail(request, pk: int):
    from reports.datasets import per_response_answers

    response = get_response(request.user, pk)
    items = per_response_answers(response)
    evaluation = Evaluation.objects.filter(response=response).prefetch_related("scores__criterion").first()
    neighbours = SurveyResponse.objects.filter(questionnaire=response.questionnaire).order_by("pk")
    prev_id = neighbours.filter(pk__lt=response.pk).values_list("pk", flat=True).last()
    next_id = neighbours.filter(pk__gt=response.pk).values_list("pk", flat=True).first()
    template = "surveys/response_print.html" if request.GET.get("print") else "surveys/response_detail.html"
    return render(request, template, {"response": response, "items": items, "evaluation": evaluation, "prev_id": prev_id, "next_id": next_id})


@staff_required
def response_compare(request):
    from reports.datasets import per_response_answers

    try:
        ids = [int(x) for x in request.GET.getlist("ids") if x.strip()][:4]
    except ValueError as exc:
        raise Http404 from exc
    responses = [get_response(request.user, i) for i in ids]
    if len(responses) < 2:
        messages.error(request, _("Select two to four responses of the same questionnaire to compare."))
        return redirect("surveys:responses")
    if len({r.questionnaire_id for r in responses}) != 1:
        messages.error(request, _("Only responses to the same questionnaire can be compared."))
        return redirect("surveys:responses")
    per = [per_response_answers(r) for r in responses]
    rows = []
    for idx, item in enumerate(per[0]):
        rows.append({"question": item["question"], "section": item["section"], "values": ["; ".join(a.display_value for a in p[idx]["answers"]) or "—" for p in per]})
    return render(request, "surveys/compare.html", {"responses": responses, "rows": rows})


@staff_required
def participant_list(request):
    visible = Questionnaire.objects.visible_to(request.user)
    qs = Participant.objects.filter(questionnaire__in=visible).select_related("questionnaire", "response")
    qid = request.GET.get("questionnaire")
    if qid and qid.isdigit():
        qs = qs.filter(questionnaire_id=int(qid))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(faculty__icontains=term) | Q(specialty__icontains=term) | Q(university__icontains=term) | Q(group_name__icontains=term))
    page = Paginator(qs.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(request, "surveys/participants.html", {"page": page, "questionnaires": visible.order_by("title"), "qid": qid, "q": term})
