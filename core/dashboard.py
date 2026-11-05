"""Data for the researcher dashboard. Every figure comes from the database.

All querysets are scoped through ``Questionnaire.objects.visible_to(user)``,
so a researcher only ever sees their own studies (administrators see all).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import median

from django.db.models import Avg, Count, F
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from evaluation.models import Evaluation
from surveys.models import EDUCATION_LEVEL_CHOICES, Questionnaire, SurveyResponse

from .models import AuditLog

TREND_DAYS = 30
SPARK_DAYS = 14

# Audit actions shown in the activity timeline → (label, icon).
ACTIVITY = {
    "questionnaire.create": (gettext_lazy("Questionnaire created"), "plus"),
    "questionnaire.import": (gettext_lazy("Questionnaire imported from Word"), "upload"),
    "questionnaire.update": (gettext_lazy("Questionnaire edited"), "builder"),
    "questionnaire.approve": (gettext_lazy("Questionnaire approved"), "check"),
    "questionnaire.publish": (gettext_lazy("Questionnaire published"), "send"),
    "questionnaire.close": (gettext_lazy("Data collection closed"), "lock"),
    "questionnaire.reopen": (gettext_lazy("Data collection reopened"), "history"),
    "questionnaire.duplicate": (gettext_lazy("Questionnaire duplicated"), "copy"),
    "evaluation.save": (gettext_lazy("Response evaluated"), "award"),
    "evaluation.auto_run": (gettext_lazy("Automatic evaluation run"), "sparkles"),
    "evaluation.criteria": (gettext_lazy("Evaluation criteria updated"), "award"),
    "report.create": (gettext_lazy("Report created"), "report"),
    "export": (gettext_lazy("Data exported"), "download"),
    "findings.generate": (gettext_lazy("Findings generated"), "brain"),
}


@dataclass
class Kpi:
    label: str
    value: str
    icon: str
    url: str
    hint: str = ""
    delta: int | None = None  # change vs the previous 7 days, in units
    spark: str = ""  # SVG polyline points (0..100 × 0..28)
    tone: str = "royal"
    count: int | None = None  # integer value for the animated counter


@dataclass
class Notice:
    level: str  # info | warning | success
    title: str
    text: str
    url: str
    icon: str


@dataclass
class Event:
    when: object
    title: str
    detail: str
    icon: str
    url: str = ""
    tone: str = "royal"


@dataclass
class Dashboard:
    kpis: list[Kpi]
    trend_chart: dict
    status_chart: dict | None
    projects: list
    recent: list
    indicators: list[dict]
    notices: list[Notice]
    events: list[Event]
    has_data: bool = False
    week: int = 0

    @property
    def chart_specs(self) -> list[dict]:
        return [c for c in (self.trend_chart, self.status_chart) if c]


def _daily(qs, field_name: str, days: int) -> list[int]:
    """Counts per calendar day for the last ``days`` days (oldest first)."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        qs.filter(**{f"{field_name}__date__gte": start})
        .annotate(d=TruncDate(field_name))
        .values("d")
        .annotate(n=Count("id"))
    )
    by_day = {r["d"]: r["n"] for r in rows}
    return [by_day.get(start + timedelta(days=i), 0) for i in range(days)]


def _spark(values: list[int]) -> str:
    if not values or max(values) == 0:
        return ""
    top = max(values)
    step = 100 / max(len(values) - 1, 1)
    return " ".join(f"{i * step:.1f},{26 - (v / top) * 24:.1f}" for i, v in enumerate(values))


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _fmt_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 1:
        return _("< 1 min")
    return _("%(m)s min") % {"m": round(minutes)}


def build(user) -> Dashboard:
    questionnaires = Questionnaire.objects.visible_to(user)
    responses = SurveyResponse.objects.filter(questionnaire__in=questionnaires)
    completed = responses.filter(status=SurveyResponse.Status.COMPLETED)
    in_progress = responses.filter(status=SurveyResponse.Status.IN_PROGRESS)
    evaluations = Evaluation.objects.filter(response__in=completed)

    n_started = responses.count()
    n_completed = completed.count()
    n_active = sum(1 for q in questionnaires.filter(status=Questionnaire.Status.PUBLISHED) if q.accepting_responses())

    started_daily = _daily(responses, "started_at", TREND_DAYS)
    completed_daily = _daily(completed, "submitted_at", TREND_DAYS)
    week, prev_week = sum(completed_daily[-7:]), sum(completed_daily[-14:-7])
    started_week, started_prev = sum(started_daily[-7:]), sum(started_daily[-14:-7])
    rate = _pct(n_completed, n_started)

    kpis = [
        Kpi(_("Active studies"), str(n_active), "flask", reverse("surveys:list") + "?status=published",
            hint=_("%(n)s in total") % {"n": questionnaires.count()}, tone="royal", count=n_active),
        Kpi(_("Participants"), str(n_started), "users", reverse("surveys:participants"),
            delta=started_week - started_prev, spark=_spark(started_daily[-SPARK_DAYS:]), tone="electric", count=n_started),
        Kpi(_("Completed responses"), str(n_completed), "inbox", reverse("surveys:responses") + "?status=completed",
            delta=week - prev_week, spark=_spark(completed_daily[-SPARK_DAYS:]), tone="success", count=n_completed),
        Kpi(_("Completion rate"), f"{rate:g}%" if rate is not None else "—", "check",
            reverse("surveys:responses"), hint=_("completed of started"), tone="navy"),
    ]

    today = timezone.localdate()
    labels = [(today - timedelta(days=TREND_DAYS - 1 - i)).strftime("%d.%m") for i in range(TREND_DAYS)]
    trend_chart = {
        "id": "chart-trend", "type": "trend", "labels": labels,
        "datasets": [
            {"label": str(_("Started")), "values": started_daily},
            {"label": str(_("Completed")), "values": completed_daily},
        ],
    }

    status_rows = (
        completed.exclude(participant__education_level="")
        .values("participant__education_level").annotate(n=Count("id")).order_by("-n")
    )
    names = dict(EDUCATION_LEVEL_CHOICES)
    status_chart = None
    if status_rows:
        status_chart = {
            "id": "chart-status", "type": "pie",
            "labels": [str(names.get(r["participant__education_level"], r["participant__education_level"])) for r in status_rows],
            "values": [r["n"] for r in status_rows],
        }

    projects = list(
        questionnaires.with_counts().filter(status__in=[Questionnaire.Status.PUBLISHED, Questionnaire.Status.APPROVED, Questionnaire.Status.DRAFT])
        .order_by(F("published_at").desc(nulls_last=True), "-updated_at")[:5]
    )
    for p in projects:
        p.rate = _pct(p.completed_count, p.participant_count) or 0

    recent = list(
        completed.select_related("questionnaire", "participant", "evaluation").order_by("-submitted_at")[:6]
    )

    durations = [
        (s - b).total_seconds() / 60
        for s, b in completed.filter(submitted_at__isnull=False).values_list("submitted_at", "started_at")[:2000]
        if s and b and s >= b
    ]
    n_evaluated = evaluations.exclude(final_score__isnull=True).count()
    n_reviewed = evaluations.filter(status=Evaluation.Status.REVIEWED).count()
    avg_score = evaluations.filter(final_score__isnull=False).aggregate(v=Avg("final_score"))["v"]
    indicators = [
        {"label": _("Completion rate"), "value": rate, "display": f"{rate:g}%" if rate is not None else "—",
         "hint": _("%(c)s of %(s)s started") % {"c": n_completed, "s": n_started}},
        {"label": _("Evaluation coverage"), "value": _pct(n_evaluated, n_completed),
         "display": f"{_pct(n_evaluated, n_completed):g}%" if n_completed else "—",
         "hint": _("%(n)s responses scored") % {"n": n_evaluated}},
        {"label": _("Reviewed by a person"), "value": _pct(n_reviewed, n_completed),
         "display": f"{_pct(n_reviewed, n_completed):g}%" if n_completed else "—",
         "hint": _("%(n)s reviewed") % {"n": n_reviewed}},
        {"label": _("Average score"), "value": float(avg_score) if avg_score is not None else None,
         "display": f"{float(avg_score):.1f}%" if avg_score is not None else "—",
         "hint": _("Median time: %(t)s") % {"t": _fmt_minutes(median(durations) if durations else None)}},
    ]

    return Dashboard(
        kpis=kpis, trend_chart=trend_chart, status_chart=status_chart, projects=projects, recent=recent,
        indicators=indicators, notices=notices(user, questionnaires, completed, in_progress),
        events=activity(user, questionnaires, completed), has_data=n_started > 0, week=week,
    )


def notices(user, questionnaires, completed, in_progress) -> list[Notice]:
    """Actionable alerts derived from the current state of the user's studies."""
    out: list[Notice] = []
    pending = completed.exclude(evaluation__status=Evaluation.Status.REVIEWED).count()
    if pending:
        out.append(Notice("warning", _("Responses await review"),
                          _("%(n)s completed responses have not been reviewed by a person yet.") % {"n": pending},
                          reverse("evaluation:queue") + "?state=pending", "award"))
    soon = timezone.now() + timedelta(days=7)
    for q in questionnaires.filter(status=Questionnaire.Status.PUBLISHED, closes_at__isnull=False,
                                   closes_at__gt=timezone.now(), closes_at__lte=soon)[:3]:
        out.append(Notice("info", _("Closes soon"),
                          _("“%(t)s” closes on %(d)s.") % {"t": q.title[:60], "d": q.closes_at.strftime("%d.%m.%Y")},
                          q.get_absolute_url(), "clock"))
    unpublished = questionnaires.filter(status__in=[Questionnaire.Status.DRAFT, Questionnaire.Status.APPROVED]).count()
    if unpublished:
        out.append(Notice("info", _("Not yet published"),
                          _("%(n)s questionnaires are drafts or approved but not published.") % {"n": unpublished},
                          reverse("surveys:list"), "files"))
    stale = in_progress.filter(last_saved_at__lt=timezone.now() - timedelta(days=1)).count()
    if stale:
        out.append(Notice("info", _("Unfinished responses"),
                          _("%(n)s participants started more than a day ago and have not submitted.") % {"n": stale},
                          reverse("surveys:responses") + "?status=in_progress", "history"))
    if not out:
        out.append(Notice("success", _("All caught up"), _("Nothing needs your attention right now."), reverse("surveys:list"), "check"))
    return out


def activity(user, questionnaires, completed, limit: int = 8) -> list[Event]:
    """Merged timeline of new responses and the user's (or, for admins, everyone's) research actions."""
    events: list[Event] = []
    for r in completed.select_related("questionnaire").order_by("-submitted_at")[:limit]:
        events.append(Event(r.submitted_at, _("New response %(ref)s") % {"ref": r.reference},
                            r.questionnaire.title, "inbox", r.get_absolute_url(), "success"))
    logs = AuditLog.objects.filter(action__in=ACTIVITY.keys()).select_related("user").order_by("-created_at")
    if not user.is_platform_admin:
        logs = logs.filter(user=user)
    for log in logs[:limit]:
        label, icon = ACTIVITY[log.action]
        who = log.user.get_username() if log.user else ""
        events.append(Event(log.created_at, str(label), who, icon))
    events.sort(key=lambda e: e.when, reverse=True)
    return events[:limit]
