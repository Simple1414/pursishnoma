"""Business logic for questionnaires and responses (views only orchestrate)."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .importer import ParsedQuestionnaire
from .models import (
    AnswerOption,
    IndividualAnswer,
    Participant,
    ParticipantField,
    Question,
    Questionnaire,
    Section,
    SurveyResponse,
)

SHORT_TEXT_MAX = 500
LONG_TEXT_MAX = 10_000

DEFAULT_PARTICIPANT_FIELDS = [
    ParticipantField.Key.NAME,
    ParticipantField.Key.AGE,
    ParticipantField.Key.GENDER,
    ParticipantField.Key.EDUCATION,
    ParticipantField.Key.UNIVERSITY,
    ParticipantField.Key.FACULTY,
    ParticipantField.Key.SPECIALTY,
    ParticipantField.Key.ACADEMIC_YEAR,
    ParticipantField.Key.GROUP,
    ParticipantField.Key.SUBJECT,
]


# --------------------------------------------------------------------------- structure
def ensure_participant_fields(questionnaire: Questionnaire, enabled: Iterable[str] = ()) -> None:
    enabled = set(enabled)
    existing = set(questionnaire.participant_fields.values_list("key", flat=True))
    to_create = [
        ParticipantField(
            questionnaire=questionnaire,
            key=key,
            label=str(ParticipantField.Key(key).label),
            enabled=key in enabled,
            required=False,
            order=i,
        )
        for i, key in enumerate(DEFAULT_PARTICIPANT_FIELDS)
        if key not in existing
    ]
    ParticipantField.objects.bulk_create(to_create)


def unique_code(questionnaire: Questionnaire, base: str, taken: set[str] | None = None) -> str:
    taken = taken if taken is not None else set(questionnaire.questions.values_list("code", flat=True))
    code = base
    n = 2
    while code in taken:
        code = f"{base}_{n}"
        n += 1
    taken.add(code)
    return code


@transaction.atomic
def create_from_parsed(parsed: ParsedQuestionnaire, owner, language: str = "tg", source_file=None) -> Questionnaire:
    """Persist an imported questionnaire as a DRAFT that must be reviewed and approved."""
    q = Questionnaire.objects.create(
        title=parsed.title[:500],
        description=parsed.description,
        instructions=parsed.instructions,
        consent_text=parsed.consent_text,
        completion_message=parsed.completion_message,
        require_consent=True,
        language=language,
        owner=owner,
        status=Questionnaire.Status.DRAFT,
        import_report=parsed.report(),
    )
    if source_file is not None:
        q.source_file.save(source_file.name, source_file, save=True)
    taken: set[str] = set()
    for s_order, ps in enumerate(parsed.sections):
        section = Section.objects.create(questionnaire=q, title=ps.title[:500], description=ps.description, order=s_order)
        for q_order, pq in enumerate(ps.questions):
            base = f"Q{pq.number}" if pq.number else f"Q{s_order + 1}_{q_order + 1}"
            question = Question.objects.create(
                questionnaire=q,
                section=section,
                number=pq.number,
                code=unique_code(q, base, taken),
                text=pq.text,
                help_text=pq.help_text,
                qtype=pq.qtype,
                required=pq.required,
                order=q_order,
            )
            AnswerOption.objects.bulk_create(
                [
                    AnswerOption(
                        question=question, text=o.text[:500], value=o.value, allows_text=o.allows_text, order=i
                    )
                    for i, o in enumerate(pq.options)
                ]
            )
    ensure_participant_fields(q, enabled=parsed.participant_fields)
    return q


def renumber(questionnaire: Questionnaire) -> None:
    """Normalise section and question ``order`` values after edits."""
    for i, section in enumerate(questionnaire.sections.all()):
        if section.order != i:
            Section.objects.filter(pk=section.pk).update(order=i)
        for j, question in enumerate(section.questions.order_by("order", "id")):
            if question.order != j:
                Question.objects.filter(pk=question.pk).update(order=j)


@transaction.atomic
def duplicate_questionnaire(source: Questionnaire, owner) -> Questionnaire:
    sections = list(source.sections.prefetch_related("questions__options"))
    fields = list(source.participant_fields.all())
    copy = Questionnaire.objects.get(pk=source.pk)
    copy.pk = None
    copy.public_id = uuid.uuid4()
    copy.title = f"{source.title} ({_('copy')})"[:500]
    copy.status = Questionnaire.Status.DRAFT
    copy.owner = owner
    copy.approved_by = None
    copy.approved_at = None
    copy.published_at = None
    copy.source_file = None
    copy.save()
    for s in sections:
        questions = list(s.questions.all())
        s.pk = None
        s.questionnaire = copy
        s.save()
        for qu in questions:
            options = list(qu.options.all())
            qu.pk = None
            qu.questionnaire = copy
            qu.section = s
            qu.save()
            for o in options:
                o.pk = None
                o.question = qu
            AnswerOption.objects.bulk_create(options)
    for f in fields:
        f.pk = None
        f.questionnaire = copy
    ParticipantField.objects.bulk_create(fields)
    # Evaluation criteria travel with the questionnaire design
    from evaluation.models import EvaluationCriterion

    criteria = list(EvaluationCriterion.objects.filter(questionnaire=source))
    for c in criteria:
        c.pk = None
        c.questionnaire = copy
    EvaluationCriterion.objects.bulk_create(criteria)
    return copy


def approve(questionnaire: Questionnaire, user) -> None:
    if not questionnaire.questions.exists():
        raise ValueError(_("A questionnaire without questions cannot be approved."))
    questionnaire.status = Questionnaire.Status.APPROVED
    questionnaire.approved_by = user
    questionnaire.approved_at = timezone.now()
    questionnaire.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])


def publish(questionnaire: Questionnaire) -> None:
    if questionnaire.approved_at is None:
        raise ValueError(_("The questionnaire must be approved before publication."))
    questionnaire.status = Questionnaire.Status.PUBLISHED
    questionnaire.is_active = True
    questionnaire.published_at = questionnaire.published_at or timezone.now()
    questionnaire.save(update_fields=["status", "is_active", "published_at", "updated_at"])


# --------------------------------------------------------------------------- answers
@dataclass
class AnswerSpec:
    option_id: int | None = None
    row_id: int | None = None
    text: str = ""
    number: Decimal | None = None
    date: dt.date | None = None
    rank: int | None = None


@dataclass
class ParsedAnswers:
    answers: dict[int, list[AnswerSpec]] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _getlist(data: Mapping, key: str) -> list[str]:
    if hasattr(data, "getlist"):
        return [v for v in data.getlist(key) if str(v).strip() != ""]
    value = data.get(key)
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _to_int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_answers(questions: Iterable[Question], data: Mapping, *, enforce_required: bool) -> ParsedAnswers:
    """Validate submitted form data against the questionnaire definition.

    ``questions`` must have ``options`` prefetched.
    """
    result = ParsedAnswers()
    for q in questions:
        key = f"q_{q.pk}"
        specs: list[AnswerSpec] = []
        error = ""
        options = {o.pk: o for o in q.options.all()}
        choice_ids = {pk for pk, o in options.items() if o.kind == AnswerOption.Kind.OPTION}

        if q.qtype in (Question.Type.SINGLE, Question.Type.YES_NO, Question.Type.LIKERT, Question.Type.MULTIPLE):
            raw = _getlist(data, key)
            ids = [_to_int(v) for v in raw]
            if any(i is None or i not in choice_ids for i in ids):
                error = _("Invalid option selected.")
            elif q.qtype != Question.Type.MULTIPLE and len(ids) > 1:
                error = _("Select only one option.")
            else:
                for oid in dict.fromkeys(ids):  # de-duplicate, keep order
                    other = ""
                    if options[oid].allows_text:
                        other = str(data.get(f"{key}_other_{oid}", "")).strip()[:SHORT_TEXT_MAX]
                    specs.append(AnswerSpec(option_id=oid, text=other))
        elif q.qtype == Question.Type.RATING:
            raw = _getlist(data, key)
            if raw:
                value = _to_int(raw[0])
                if value is None or value not in q.rating_values():
                    error = _("Choose a value on the scale.")
                else:
                    specs.append(AnswerSpec(number=Decimal(value)))
        elif q.qtype == Question.Type.NUMBER:
            raw = _getlist(data, key)
            if raw:
                try:
                    number = Decimal(str(raw[0]).replace(",", ".").strip())
                    if not number.is_finite() or abs(number) >= Decimal("1e13"):
                        raise InvalidOperation
                    specs.append(AnswerSpec(number=number))
                except InvalidOperation:
                    error = _("Enter a valid number.")
        elif q.qtype == Question.Type.DATE:
            raw = _getlist(data, key)
            if raw:
                try:
                    specs.append(AnswerSpec(date=dt.date.fromisoformat(str(raw[0]).strip())))
                except ValueError:
                    error = _("Enter a valid date (YYYY-MM-DD).")
        elif q.qtype in (Question.Type.SHORT_TEXT, Question.Type.LONG_TEXT):
            raw = _getlist(data, key)
            limit = SHORT_TEXT_MAX if q.qtype == Question.Type.SHORT_TEXT else LONG_TEXT_MAX
            if raw:
                text = str(raw[0]).strip()
                if len(text) > limit:
                    error = _("The answer is too long (maximum %(n)s characters).") % {"n": limit}
                elif text:
                    specs.append(AnswerSpec(text=text))
        elif q.qtype == Question.Type.MATRIX:
            rows = [o for o in options.values() if o.kind == AnswerOption.Kind.ROW]
            cols = {o.pk for o in options.values() if o.kind == AnswerOption.Kind.COLUMN}
            missing_row = False
            for row in rows:
                raw = _getlist(data, f"{key}_r_{row.pk}")
                if not raw:
                    missing_row = True
                    continue
                col = _to_int(raw[0])
                if col not in cols:
                    error = _("Invalid option selected.")
                    break
                specs.append(AnswerSpec(option_id=col, row_id=row.pk))
            if not error and enforce_required and q.required and missing_row and rows:
                error = _("Answer every row.")
        elif q.qtype == Question.Type.RANKING:
            ranks: dict[int, int] = {}
            for oid in choice_ids:
                raw = _getlist(data, f"{key}_rank_{oid}")
                if raw:
                    rank = _to_int(raw[0])
                    if rank is None or not 1 <= rank <= len(choice_ids):
                        error = _("Ranks must be between 1 and %(n)s.") % {"n": len(choice_ids)}
                        break
                    ranks[oid] = rank
            if not error and len(set(ranks.values())) != len(ranks):
                error = _("Each rank can be used only once.")
            if not error:
                if enforce_required and q.required and ranks and len(ranks) != len(choice_ids):
                    error = _("Rank every option.")
                specs = [AnswerSpec(option_id=oid, rank=r) for oid, r in sorted(ranks.items(), key=lambda x: x[1])]

        if not error and enforce_required and q.required and not specs:
            error = _("This question is required.")
        if error:
            result.errors[q.pk] = error
        elif specs:
            result.answers[q.pk] = specs
    return result


def _write_answers(response: SurveyResponse, parsed: ParsedAnswers, question_ids: Iterable[int]) -> None:
    question_ids = list(question_ids)
    IndividualAnswer.objects.filter(response=response, question_id__in=question_ids).delete()
    rows = [
        IndividualAnswer(
            response=response,
            question_id=qid,
            option_id=s.option_id,
            row_id=s.row_id,
            text_value=s.text,
            number_value=s.number,
            date_value=s.date,
            rank=s.rank,
        )
        for qid, specs in parsed.answers.items()
        for s in specs
    ]
    IndividualAnswer.objects.bulk_create(rows)


def questions_for(questionnaire: Questionnaire):
    return (
        Question.objects.filter(questionnaire=questionnaire)
        .select_related("section")
        .prefetch_related("options")
        .order_by("section__order", "section_id", "order", "id")
    )


@transaction.atomic
def autosave(response: SurveyResponse, data: Mapping) -> ParsedAnswers:
    """Save valid answers of an in-progress response (partial, no required check)."""
    locked = SurveyResponse.objects.select_for_update().get(pk=response.pk)
    if locked.is_completed:
        return ParsedAnswers()
    questions = list(questions_for(locked.questionnaire))
    present = [q for q in questions if any(str(k).startswith(f"q_{q.pk}") for k in data.keys())]
    parsed = parse_answers(present, data, enforce_required=False)
    valid_ids = [q.pk for q in present if q.pk not in parsed.errors]
    _write_answers(locked, ParsedAnswers(answers={k: v for k, v in parsed.answers.items() if k in valid_ids}), valid_ids)
    SurveyResponse.objects.filter(pk=locked.pk).update(last_saved_at=timezone.now())
    return parsed


@transaction.atomic
def submit(response: SurveyResponse, data: Mapping, token: uuid.UUID | None) -> tuple[bool, ParsedAnswers]:
    """Validate and finalise a response. Idempotent for repeated submissions."""
    locked = SurveyResponse.objects.select_for_update().get(pk=response.pk)
    if locked.is_completed:
        return True, ParsedAnswers()
    questions = list(questions_for(locked.questionnaire))
    parsed = parse_answers(questions, data, enforce_required=True)
    if not parsed.is_valid:
        # Keep everything that is valid so nothing typed is lost.
        valid_ids = [q.pk for q in questions if q.pk not in parsed.errors]
        _write_answers(locked, ParsedAnswers(answers={k: v for k, v in parsed.answers.items() if k in valid_ids}), valid_ids)
        return False, parsed
    _write_answers(locked, parsed, [q.pk for q in questions])
    locked.status = SurveyResponse.Status.COMPLETED
    locked.submitted_at = timezone.now()
    locked.submission_token = token
    locked.save(update_fields=["status", "submitted_at", "submission_token", "last_saved_at"])
    return True, parsed


@transaction.atomic
def start_response(questionnaire: Questionnaire, participant_data: dict, *, consent: bool, ip_hash: str, language: str) -> SurveyResponse:
    standard = {k: v for k, v in participant_data.items() if k in Participant.STANDARD_FIELDS}
    extra = {k: v for k, v in participant_data.items() if k not in Participant.STANDARD_FIELDS}
    participant = Participant.objects.create(
        questionnaire=questionnaire,
        consent_given=consent,
        consent_at=timezone.now() if consent else None,
        ip_hash=ip_hash,
        extra=extra,
        **standard,
    )
    return SurveyResponse.objects.create(questionnaire=questionnaire, participant=participant, language=language)


def existing_answer_map(response: SurveyResponse) -> dict[str, list[str]]:
    """Form-field values of saved answers, used to re-populate the form."""
    values: dict[str, list[str]] = {}
    for a in response.answers.select_related("question"):
        key = f"q_{a.question_id}"
        q = a.question
        if q.qtype == Question.Type.MATRIX and a.row_id:
            values[f"{key}_r_{a.row_id}"] = [str(a.option_id)]
        elif q.qtype == Question.Type.RANKING and a.option_id:
            values[f"{key}_rank_{a.option_id}"] = [str(a.rank)]
        elif a.option_id:
            values.setdefault(key, []).append(str(a.option_id))
            if a.text_value:
                values[f"{key}_other_{a.option_id}"] = [a.text_value]
        elif a.number_value is not None:
            values[key] = [f"{a.number_value.normalize():f}"]
        elif a.date_value:
            values[key] = [a.date_value.isoformat()]
        else:
            values[key] = [a.text_value]
    return values
