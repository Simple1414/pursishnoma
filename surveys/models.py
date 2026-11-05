"""Questionnaire structure and participant response storage.

Original participant answers (``IndividualAnswer``) are immutable once the
response is completed; evaluations live in the ``evaluation`` app.
"""
from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def import_upload_path(instance, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return f"imports/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class QuestionnaireQuerySet(models.QuerySet):
    def visible_to(self, user) -> "QuestionnaireQuerySet":
        if not user or not user.is_authenticated or not getattr(user, "is_staff_member", False):
            return self.none()
        if user.is_platform_admin:
            return self
        return self.filter(owner=user)

    def with_counts(self) -> "QuestionnaireQuerySet":
        return self.annotate(
            participant_count=Count("responses", distinct=True),
            completed_count=Count(
                "responses", filter=Q(responses__status="completed"), distinct=True
            ),
            question_count=Count("questions", distinct=True),
        )

    def published(self) -> "QuestionnaireQuerySet":
        return self.filter(status=Questionnaire.Status.PUBLISHED, is_active=True)


class Questionnaire(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        APPROVED = "approved", _("Approved")
        PUBLISHED = "published", _("Published")
        CLOSED = "closed", _("Closed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(_("title"), max_length=500)
    description = models.TextField(_("description"), blank=True)
    objective = models.TextField(_("research objective"), blank=True)
    author = models.CharField(_("research author"), max_length=255, blank=True)
    instructions = models.TextField(_("participation instructions"), blank=True)
    consent_text = models.TextField(_("informed consent statement"), blank=True)
    require_consent = models.BooleanField(_("require informed consent"), default=True)
    completion_message = models.TextField(_("completion message"), blank=True)
    estimated_minutes = models.PositiveSmallIntegerField(_("estimated completion time (minutes)"), default=15)
    language = models.CharField(_("questionnaire language"), max_length=5, choices=settings.LANGUAGES, default="tg")
    status = models.CharField(_("status"), max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_active = models.BooleanField(_("active"), default=True)
    opens_at = models.DateTimeField(_("opening date"), null=True, blank=True)
    closes_at = models.DateTimeField(_("closing date"), null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="questionnaires")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    source_file = models.FileField(upload_to=import_upload_path, null=True, blank=True)
    import_report = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = QuestionnaireQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True) | Q(closes_at__isnull=True) | Q(closes_at__gt=models.F("opens_at")),
                name="questionnaire_close_after_open",
            )
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("surveys:detail", args=[self.pk])

    def get_public_url(self) -> str:
        return reverse("public:landing", args=[self.public_id])

    @property
    def is_editable(self) -> bool:
        """Structure is frozen once any response exists (protects data integrity)."""
        return not self.responses.exists()

    def accepting_responses(self, now=None) -> bool:
        now = now or timezone.now()
        if self.status != self.Status.PUBLISHED or not self.is_active:
            return False
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now >= self.closes_at:
            return False
        return True

    def can_be_managed_by(self, user) -> bool:
        return bool(
            user and user.is_authenticated and (user.is_platform_admin or (user.is_staff_member and self.owner_id == user.pk))
        )


class Section(models.Model):
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(_("title"), max_length=500)
    description = models.TextField(_("description"), blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.title


class Question(models.Model):
    class Type(models.TextChoices):
        SINGLE = "single", _("Single choice")
        MULTIPLE = "multiple", _("Multiple choice")
        YES_NO = "yes_no", _("Yes / No")
        LIKERT = "likert", _("Likert scale")
        RATING = "rating", _("Rating scale")
        SHORT_TEXT = "short_text", _("Short text")
        LONG_TEXT = "long_text", _("Long text")
        NUMBER = "number", _("Numeric answer")
        DATE = "date", _("Date")
        MATRIX = "matrix", _("Matrix")
        RANKING = "ranking", _("Ranking")

    CHOICE_TYPES = {Type.SINGLE, Type.MULTIPLE, Type.YES_NO, Type.LIKERT}
    OPTION_TYPES = CHOICE_TYPES | {Type.MATRIX, Type.RANKING}
    NUMERIC_TYPES = {Type.LIKERT, Type.RATING, Type.NUMBER}
    TEXT_TYPES = {Type.SHORT_TEXT, Type.LONG_TEXT}

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="questions")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="questions")
    number = models.CharField(_("number"), max_length=20, blank=True, help_text=_("Original question number"))
    code = models.CharField(_("variable code"), max_length=40)
    text = models.TextField(_("question text"))
    help_text = models.TextField(_("help text"), blank=True)
    qtype = models.CharField(_("question type"), max_length=20, choices=Type.choices, default=Type.SINGLE)
    required = models.BooleanField(_("required"), default=True)
    order = models.PositiveIntegerField(default=0)
    scale_min = models.SmallIntegerField(_("scale minimum"), null=True, blank=True)
    scale_max = models.SmallIntegerField(_("scale maximum"), null=True, blank=True)
    scale_min_label = models.CharField(_("minimum label"), max_length=120, blank=True)
    scale_max_label = models.CharField(_("maximum label"), max_length=120, blank=True)

    class Meta:
        ordering = ["section__order", "order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["questionnaire", "code"], name="unique_question_code"),
            models.CheckConstraint(
                condition=Q(scale_min__isnull=True) | Q(scale_max__isnull=True) | Q(scale_max__gt=models.F("scale_min")),
                name="question_scale_valid",
            ),
        ]
        indexes = [models.Index(fields=["questionnaire", "qtype"])]

    def __str__(self) -> str:
        return f"{self.number}. {self.text[:80]}" if self.number else self.text[:80]

    def clean(self) -> None:
        if self.section_id and self.questionnaire_id and self.section.questionnaire_id != self.questionnaire_id:
            raise ValidationError(_("Section belongs to another questionnaire."))
        if self.qtype == self.Type.RATING and (self.scale_min is None or self.scale_max is None):
            raise ValidationError(_("Rating questions need a scale minimum and maximum."))

    @property
    def uses_options(self) -> bool:
        return self.qtype in self.OPTION_TYPES

    @property
    def is_numeric(self) -> bool:
        return self.qtype in self.NUMERIC_TYPES

    @property
    def is_text(self) -> bool:
        return self.qtype in self.TEXT_TYPES

    @property
    def is_categorical(self) -> bool:
        return self.qtype in {self.Type.SINGLE, self.Type.YES_NO, self.Type.LIKERT}

    @property
    def display_label(self) -> str:
        return f"{self.number}. {self.text}" if self.number else self.text

    def choice_options(self):
        return [o for o in self.options.all() if o.kind == AnswerOption.Kind.OPTION]

    def matrix_rows(self):
        return [o for o in self.options.all() if o.kind == AnswerOption.Kind.ROW]

    def matrix_columns(self):
        return [o for o in self.options.all() if o.kind == AnswerOption.Kind.COLUMN]

    def rating_values(self) -> list[int]:
        if self.scale_min is None or self.scale_max is None:
            return []
        return list(range(self.scale_min, self.scale_max + 1))


class AnswerOption(models.Model):
    class Kind(models.TextChoices):
        OPTION = "option", _("Answer option")
        ROW = "row", _("Matrix row")
        COLUMN = "column", _("Matrix column")

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OPTION)
    text = models.CharField(_("text"), max_length=500)
    value = models.IntegerField(_("numeric code"), null=True, blank=True)
    allows_text = models.BooleanField(_("allows free text ('other')"), default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        indexes = [models.Index(fields=["question", "kind"])]

    def __str__(self) -> str:
        return self.text


# Stored values are fixed Tajik texts (the label shown follows the interface language),
# so analytics groups stay identical whatever language a participant used.
EDUCATION_LEVEL_CHOICES = [
    ("Хонандаи мактаб", _("School pupil")),
    ("Донишҷӯи коллеҷ / литсей", _("College / lyceum student")),
    ("Донишҷӯи донишгоҳ (бакалавр)", _("University student (bachelor)")),
    ("Магистрант / докторант", _("Master's or doctoral student")),
    ("Дигар", _("Other")),
]


class ParticipantField(models.Model):
    """Which participant attributes a questionnaire collects (all optional by default)."""

    class Key(models.TextChoices):
        NAME = "name", _("Participant name")
        AGE = "age", _("Age")
        GENDER = "gender", _("Gender")
        EDUCATION = "education_level", _("Level of education")
        UNIVERSITY = "university", _("Educational institution")
        FACULTY = "faculty", _("Faculty / department")
        SPECIALTY = "specialty", _("Specialty / field of study")
        ACADEMIC_YEAR = "academic_year", _("Year or grade")
        GROUP = "group_name", _("Group")
        CUSTOM = "custom", _("Custom field")

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="participant_fields")
    key = models.CharField(max_length=32, choices=Key.choices)
    label = models.CharField(_("label"), max_length=200)
    enabled = models.BooleanField(_("collect"), default=False)
    required = models.BooleanField(_("required"), default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["questionnaire", "label"], name="unique_participant_field_label")]

    def __str__(self) -> str:
        return self.label

    @property
    def form_name(self) -> str:
        return f"pf_{self.pk}"


class Participant(models.Model):
    """Optional demographic data; anonymous participation leaves all fields blank."""

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.PROTECT, related_name="participants")
    name = models.CharField(max_length=255, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=50, blank=True)
    education_level = models.CharField(max_length=120, blank=True)
    university = models.CharField(max_length=255, blank=True)
    faculty = models.CharField(max_length=255, blank=True, db_index=True)
    specialty = models.CharField(max_length=255, blank=True, db_index=True)
    academic_year = models.CharField(max_length=20, blank=True, db_index=True)
    group_name = models.CharField(max_length=60, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    consent_given = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    STANDARD_FIELDS = (
        "name", "age", "gender", "education_level", "university", "faculty", "specialty", "academic_year", "group_name",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or _("Anonymous participant #%(id)s") % {"id": self.pk}

    @property
    def is_anonymous(self) -> bool:
        return not self.name


class SurveyResponse(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", _("Incomplete")
        COMPLETED = "completed", _("Completed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.PROTECT, related_name="responses")
    participant = models.OneToOneField(Participant, on_delete=models.PROTECT, related_name="response")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)
    language = models.CharField(max_length=5, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_saved_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    submission_token = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["questionnaire", "status", "submitted_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="in_progress") | Q(submitted_at__isnull=False),
                name="completed_response_has_submitted_at",
            )
        ]

    def __str__(self) -> str:
        return self.reference

    @property
    def reference(self) -> str:
        """Human-friendly unique identifier, e.g. R-000042."""
        return f"R-{self.pk:06d}" if self.pk else "R-new"

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def duration_seconds(self) -> int | None:
        if self.submitted_at and self.started_at:
            return int((self.submitted_at - self.started_at).total_seconds())
        return None

    def get_absolute_url(self) -> str:
        return reverse("surveys:response_detail", args=[self.pk])


class ImmutableAnswerError(Exception):
    """Raised when code tries to change an answer of a completed response."""


class IndividualAnswer(models.Model):
    """One stored fact of an answer. Choice answers store one row per selected option."""

    response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="answers")
    option = models.ForeignKey(AnswerOption, null=True, blank=True, on_delete=models.PROTECT, related_name="answers")
    row = models.ForeignKey(
        AnswerOption, null=True, blank=True, on_delete=models.PROTECT, related_name="row_answers"
    )
    text_value = models.TextField(blank=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    date_value = models.DateField(null=True, blank=True)
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question__section__order", "question__order", "id"]
        indexes = [
            models.Index(fields=["question", "option"]),
            models.Index(fields=["response", "question"]),
        ]

    def __str__(self) -> str:
        return f"{self.response_id}:{self.question_id}"

    def _guard(self) -> None:
        status = SurveyResponse.objects.filter(pk=self.response_id).values_list("status", flat=True).first()
        if status == SurveyResponse.Status.COMPLETED:
            raise ImmutableAnswerError("Answers of a completed response cannot be changed.")

    def save(self, *args, **kwargs) -> None:
        self._guard()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        return super().delete(*args, **kwargs)

    @property
    def display_value(self) -> str:
        if self.option_id:
            base = self.option.text
            if self.row_id:
                base = f"{self.row.text}: {base}"
            if self.rank:
                base = f"{self.rank}. {base}"
            if self.text_value:
                base = f"{base} — {self.text_value}"
            return base
        if self.number_value is not None:
            return f"{self.number_value.normalize():f}"
        if self.date_value:
            return self.date_value.isoformat()
        return self.text_value
