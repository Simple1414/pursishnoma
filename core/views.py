from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from evaluation.models import Evaluation
from surveys.models import Participant, Questionnaire, SurveyResponse

from .audit import log_action
from .forms import LoginForm, SiteSettingsForm, UserCreateForm, UserUpdateForm
from .models import AuditLog, SiteSettings, User
from .permissions import admin_required, staff_required
from .ratelimit import is_rate_limited

logger = logging.getLogger(__name__)


def home(request):
    site = SiteSettings.load()
    public = Questionnaire.objects.filter(status__in=[Questionnaire.Status.PUBLISHED, Questionnaire.Status.CLOSED])
    public_stats = None
    if site.show_public_statistics:
        public_stats = {
            "studies": public.count(),
            "participants": Participant.objects.filter(questionnaire__in=public).count(),
            "responses": SurveyResponse.objects.filter(
                questionnaire__in=public, status=SurveyResponse.Status.COMPLETED
            ).count(),
            "open": sum(1 for q in Questionnaire.objects.published() if q.accepting_responses()),
        }
    candidates = (
        Questionnaire.objects.published()
        .annotate(
            n_completed=Count("responses", filter=Q(responses__status=SurveyResponse.Status.COMPLETED), distinct=True),
            n_questions=Count("questions", distinct=True),
        )
        .order_by("-published_at")[:6]
    )
    open_studies = [q for q in candidates if q.accepting_responses()]
    return render(request, "core/home.html", {"public_stats": public_stats, "open_studies": open_studies})


def privacy(request):
    """Public privacy notice describing how participant data is handled."""
    return render(request, "core/privacy.html")


def guide(request):
    """Public one-minute video guide for first-time participants."""
    return render(request, "core/guide.html")


class RateLimitedLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if is_rate_limited(request, "login", settings.RATE_LIMIT_LOGIN):
            log_action(request, "login.rate_limited")
            messages.error(request, _("Too many login attempts. Please wait and try again."))
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, "login.success", user=form.get_user())
        return response

    def form_invalid(self, form):
        log_action(self.request, "login.failed", username=self.request.POST.get("username", "")[:150])
        return super().form_invalid(form)


@staff_required
def dashboard(request):
    questionnaires = Questionnaire.objects.visible_to(request.user)
    responses = SurveyResponse.objects.filter(questionnaire__in=questionnaires)
    completed = responses.filter(status=SurveyResponse.Status.COMPLETED)
    evaluations = Evaluation.objects.filter(response__in=completed)
    reviewed = evaluations.filter(status=Evaluation.Status.REVIEWED)
    cards = {
        "questionnaires": questionnaires.count(),
        "participants": responses.count(),
        "completed": completed.count(),
        "incomplete": responses.filter(status=SurveyResponse.Status.IN_PROGRESS).count(),
        "awaiting": completed.exclude(evaluation__status=Evaluation.Status.REVIEWED).count(),
        "evaluated": reviewed.count(),
        "avg_score": evaluations.filter(final_score__isnull=False).aggregate(v=Avg("final_score"))["v"],
    }
    recent_responses = completed.select_related("questionnaire", "participant").order_by("-submitted_at")[:8]
    top = questionnaires.with_counts().order_by("-updated_at")[:6]
    return render(request, "core/dashboard.html", {"cards": cards, "recent_responses": recent_responses, "questionnaires": top})


# --------------------------------------------------------------------------- users
@admin_required
def user_list(request):
    users = User.objects.annotate(n_questionnaires=Count("questionnaires")).order_by("username")
    q = request.GET.get("q", "").strip()
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    page = Paginator(users, 25).get_page(request.GET.get("page"))
    return render(request, "core/users.html", {"page": page, "q": q})


@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        log_action(request, "user.create", user, role=user.role)
        messages.success(request, _("User created."))
        return redirect("core:users")
    return render(request, "core/user_form.html", {"form": form, "title": _("New user")})


@admin_required
def user_edit(request, pk: int):
    target = get_object_or_404(User, pk=pk)
    form = UserUpdateForm(request.POST or None, instance=target)
    if request.method == "POST" and form.is_valid():
        if target == request.user and (not form.cleaned_data["is_active"] or form.cleaned_data["role"] != User.Role.ADMIN) and not target.is_superuser:
            messages.error(request, _("You cannot deactivate or demote your own account."))
        else:
            form.save()
            log_action(request, "user.update", target, role=target.role, active=target.is_active)
            messages.success(request, _("User updated."))
            return redirect("core:users")
    return render(request, "core/user_form.html", {"form": form, "title": target.username, "target": target})


# --------------------------------------------------------------------------- settings & security
@admin_required
def site_settings(request):
    obj = SiteSettings.load()
    form = SiteSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "settings.update", obj)
        messages.success(request, _("Settings saved."))
        return redirect("core:settings")
    ai = {"provider": settings.AI_PROVIDER, "configured": settings.AI_PROVIDER == "anthropic" and bool(settings.AI_API_KEY), "model": settings.AI_MODEL or "claude-opus-5-5"}
    return render(request, "core/settings.html", {"form": form, "ai": ai})


@admin_required
def security(request):
    logs = AuditLog.objects.select_related("user")
    action = request.GET.get("action", "").strip()
    if action:
        logs = logs.filter(action__startswith=action)
    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    checks = [
        (_("Debug mode disabled"), not settings.DEBUG),
        (_("HTTPS enforced (secure cookies, HSTS)"), getattr(settings, "USE_HTTPS", False)),
        (_("Secret key configured from environment"), bool(settings.SECRET_KEY) and "insecure" not in settings.SECRET_KEY),
        (_("PostgreSQL database"), settings.DATABASES["default"]["ENGINE"].endswith("postgresql")),
        (_("Password hashing: PBKDF2"), settings.PASSWORD_HASHERS[0].endswith("PBKDF2PasswordHasher")),
    ]
    failed_logins = AuditLog.objects.filter(action__in=["login.failed", "login.rate_limited"]).count()
    return render(request, "core/security.html", {"page": page, "checks": checks, "failed_logins": failed_logins, "action": action})


def healthz(request):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


# --------------------------------------------------------------------------- errors
def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)
