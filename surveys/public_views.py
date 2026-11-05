"""Public participant flow: landing → consent/start → fill (autosave) → submit → done.

Responses are bound to the participant's session; a participant can never
open another participant's response. Every answer is written to PostgreSQL
(autosave on each step and on submit) — nothing important lives in the browser.
"""
from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.audit import client_ip, hash_ip
from core.ratelimit import is_rate_limited

from . import services
from .forms import StartForm
from .models import Questionnaire, SurveyResponse

logger = logging.getLogger(__name__)


def _session_key(qn: Questionnaire) -> str:
    return f"rs_response_{qn.pk}"


def _get_questionnaire(public_id) -> Questionnaire:
    return get_object_or_404(Questionnaire, public_id=public_id)


def _current_response(request, qn: Questionnaire) -> SurveyResponse | None:
    rid = request.session.get(_session_key(qn))
    if not rid:
        return None
    return SurveyResponse.objects.filter(pk=rid, questionnaire=qn).select_related("participant").first()


def build_fill_context(qn: Questionnaire, values: dict, errors: dict) -> dict:
    sections = list(qn.sections.prefetch_related("questions__options"))
    total = sum(len(s.questions.all()) for s in sections)
    return {
        "questionnaire": qn,
        "sections": sections,
        "values": values,
        "errors": errors,
        "total_questions": total,
        "submission_token": uuid.uuid4(),
    }


def landing(request, public_id):
    qn = _get_questionnaire(public_id)
    if qn.status == Questionnaire.Status.DRAFT or qn.status == Questionnaire.Status.APPROVED:
        raise Http404
    current = _current_response(request, qn)
    if current and current.is_completed:
        return redirect("public:done", public_id=qn.public_id)
    if current and qn.accepting_responses():
        return redirect("public:fill", public_id=qn.public_id)
    form = StartForm(questionnaire=qn)
    not_yet_open = bool(qn.opens_at and timezone.now() < qn.opens_at)
    return render(request, "public/landing.html", {"questionnaire": qn, "form": form, "open": qn.accepting_responses(), "not_yet_open": not_yet_open})


@require_POST
def start(request, public_id):
    qn = _get_questionnaire(public_id)
    if not qn.accepting_responses():
        messages.error(request, _("This questionnaire is not accepting responses."))
        return redirect("public:landing", public_id=qn.public_id)
    if _current_response(request, qn):
        return redirect("public:fill", public_id=qn.public_id)
    form = StartForm(request.POST, questionnaire=qn)
    if not form.is_valid():
        return render(request, "public/landing.html", {"questionnaire": qn, "form": form, "open": True}, status=400)
    if is_rate_limited(request, f"start:{qn.pk}", settings.RATE_LIMIT_START):
        messages.error(request, _("Too many attempts from your network. Please try again later."))
        return redirect("public:landing", public_id=qn.public_id)
    response = services.start_response(
        qn,
        form.participant_data(),
        consent=bool(form.cleaned_data.get("consent")) or not qn.require_consent,
        ip_hash=hash_ip(client_ip(request)),
        language=get_language() or qn.language,
    )
    request.session[_session_key(qn)] = response.pk
    request.session.set_expiry(60 * 60 * 24 * 7)
    return redirect("public:fill", public_id=qn.public_id)


def fill(request, public_id):
    qn = _get_questionnaire(public_id)
    response = _current_response(request, qn)
    if response is None:
        return redirect("public:landing", public_id=qn.public_id)
    if response.is_completed:
        return redirect("public:done", public_id=qn.public_id)
    if not qn.accepting_responses():
        messages.error(request, _("This questionnaire is no longer accepting responses."))
        return redirect("public:landing", public_id=qn.public_id)
    context = build_fill_context(qn, services.existing_answer_map(response), {})
    context["response"] = response
    return render(request, "public/fill.html", context)


@require_POST
def autosave(request, public_id):
    qn = _get_questionnaire(public_id)
    response = _current_response(request, qn)
    if response is None or response.is_completed or not qn.accepting_responses():
        return JsonResponse({"ok": False, "error": "no_active_response"}, status=409)
    if is_rate_limited(request, f"save:{response.pk}", "240/600", key=str(response.pk)):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    parsed = services.autosave(response, request.POST)
    return JsonResponse({"ok": True, "errors": {str(k): v for k, v in parsed.errors.items()}})


@require_POST
def submit(request, public_id):
    qn = _get_questionnaire(public_id)
    response = _current_response(request, qn)
    if response is None:
        return redirect("public:landing", public_id=qn.public_id)
    if response.is_completed:
        return redirect("public:done", public_id=qn.public_id)
    if not qn.accepting_responses():
        messages.error(request, _("This questionnaire is no longer accepting responses."))
        return redirect("public:landing", public_id=qn.public_id)
    if is_rate_limited(request, "submit", settings.RATE_LIMIT_SUBMIT):
        messages.error(request, _("Too many submissions from your network. Please try again later."))
        return redirect("public:fill", public_id=qn.public_id)
    try:
        token = uuid.UUID(request.POST.get("submission_token", ""))
    except ValueError:
        token = uuid.uuid4()
    if SurveyResponse.objects.filter(submission_token=token).exclude(pk=response.pk).exists():
        token = uuid.uuid4()
    ok, parsed = services.submit(response, request.POST, token)
    if ok:
        _auto_evaluate_safely(response)
        return redirect("public:done", public_id=qn.public_id)
    context = build_fill_context(qn, _post_values(request.POST), parsed.errors)
    context["response"] = response
    context["has_errors"] = True
    context["error_step"] = next(
        (i for i, sec in enumerate(context["sections"]) if any(q.pk in parsed.errors for q in sec.questions.all())), 0
    )
    messages.error(request, _("Please answer the highlighted questions."))
    return render(request, "public/fill.html", context, status=400)


def _auto_evaluate_safely(response: SurveyResponse) -> None:
    """Automatic evaluation must never break or slow a participant's submission."""
    try:
        from evaluation.services import auto_evaluate

        response.refresh_from_db()
        auto_evaluate(response)
    except Exception:  # noqa: BLE001
        logger.exception("automatic evaluation failed for response %s", response.pk)


def done(request, public_id):
    qn = _get_questionnaire(public_id)
    response = _current_response(request, qn)
    if response is None or not response.is_completed:
        return redirect("public:landing", public_id=qn.public_id)
    return render(request, "public/done.html", {"questionnaire": qn, "response": response})


def _post_values(post) -> dict[str, list[str]]:
    return {k: post.getlist(k) for k in post.keys() if k.startswith("q_")}
