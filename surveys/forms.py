from __future__ import annotations

import os

from django import forms
from django.conf import settings
from django.forms import inlineformset_factory, modelformset_factory
from django.utils.translation import gettext_lazy as _

from core.forms import StyledFormMixin

from .participant_roles import CONDITIONAL_KEYS, ROLE_FIELDS
from .models import EDUCATION_LEVEL_CHOICES, AnswerOption, Participant, ParticipantField, Question, Questionnaire, Section

DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class QuestionnaireForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Questionnaire
        fields = [
            "title", "description", "show_institution_emblem", "objective", "author", "language", "estimated_minutes",
            "instructions", "consent_text", "require_consent", "completion_message", "opens_at", "closes_at",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "objective": forms.Textarea(attrs={"rows": 3}),
            "instructions": forms.Textarea(attrs={"rows": 5}),
            "consent_text": forms.Textarea(attrs={"rows": 4}),
            "completion_message": forms.Textarea(attrs={"rows": 2}),
            "opens_at": DATETIME_WIDGET,
            "closes_at": DATETIME_WIDGET,
        }

    def clean(self):
        data = super().clean()
        if data.get("opens_at") and data.get("closes_at") and data["closes_at"] <= data["opens_at"]:
            self.add_error("closes_at", _("The closing date must be after the opening date."))
        return data


class ImportForm(StyledFormMixin, forms.Form):
    document = forms.FileField(label=_("Word questionnaire (.docx)"))
    language = forms.ChoiceField(label=_("Questionnaire language"), choices=settings.LANGUAGES, initial="tg")

    def clean_document(self):
        f = self.cleaned_data["document"]
        ext = os.path.splitext(f.name)[1].lower()
        if ext != ".docx":
            raise forms.ValidationError(_("Only .docx files are accepted."))
        if f.size > settings.QUESTIONNAIRE_IMPORT_MAX_BYTES:
            raise forms.ValidationError(_("The file is too large (maximum %(mb)s MB).") % {"mb": settings.QUESTIONNAIRE_IMPORT_MAX_BYTES // (1024 * 1024)})
        head = f.read(4)
        f.seek(0)
        if head != b"PK\x03\x04":
            raise forms.ValidationError(_("The file content is not a valid .docx document."))
        return f


class SectionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Section
        fields = ["title", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class QuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Question
        fields = [
            "section", "number", "code", "text", "help_text", "qtype", "required",
            "scale_min", "scale_max", "scale_min_label", "scale_max_label",
        ]
        widgets = {"text": forms.Textarea(attrs={"rows": 3}), "help_text": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, questionnaire: Questionnaire, locked: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.questionnaire = questionnaire
        self.fields["section"].queryset = questionnaire.sections.all()
        if locked:
            # Answers exist: the type and code define stored data and cannot change.
            for name in ("qtype", "code", "scale_min", "scale_max"):
                self.fields[name].disabled = True

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.replace("_", "").isalnum():
            raise forms.ValidationError(_("Use letters, digits and underscores only."))
        qs = Question.objects.filter(questionnaire=self.questionnaire, code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(_("This code is already used in the questionnaire."))
        return code

    def clean(self):
        data = super().clean()
        if data.get("qtype") == Question.Type.RATING:
            lo, hi = data.get("scale_min"), data.get("scale_max")
            if lo is None or hi is None:
                self.add_error("scale_max", _("Rating questions need a scale minimum and maximum."))
            elif hi <= lo or hi - lo > 20:
                self.add_error("scale_max", _("The scale must have between 2 and 21 points."))
        return data


class OptionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AnswerOption
        fields = ["kind", "text", "value", "allows_text", "order"]


OptionFormSet = inlineformset_factory(
    Question, AnswerOption, form=OptionForm, extra=2, can_delete=True, min_num=0
)


class ParticipantFieldForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ParticipantField
        fields = ["label", "enabled", "required", "order"]


ParticipantFieldFormSet = modelformset_factory(ParticipantField, form=ParticipantFieldForm, extra=0)


class CustomParticipantFieldForm(StyledFormMixin, forms.Form):
    label = forms.CharField(label=_("Custom field label"), max_length=200)


class StartForm(forms.Form):
    """Consent + configured participant fields (built dynamically).

    If the questionnaire collects "Your status", the institution fields depend on the
    chosen status (see ``participant_roles``): only the relevant ones are shown,
    validated and stored.
    """

    def __init__(self, *args, questionnaire: Questionnaire, **kwargs):
        super().__init__(*args, **kwargs)
        self.questionnaire = questionnaire
        self.field_defs = list(questionnaire.participant_fields.filter(enabled=True))
        self.status_field = next((pf for pf in self.field_defs if pf.key == ParticipantField.Key.EDUCATION), None)
        hints = {
            ParticipantField.Key.UNIVERSITY: _("Name of your university, college or school"),
            ParticipantField.Key.ACADEMIC_YEAR: _("For example: 2nd year or 10th grade"),
        }
        for pf in self.field_defs:
            if pf.key == ParticipantField.Key.AGE:
                field = forms.IntegerField(min_value=7, max_value=120)
            elif pf.key == ParticipantField.Key.EDUCATION:
                field = forms.ChoiceField(choices=[("", "—")] + EDUCATION_LEVEL_CHOICES)
                field.widget.attrs["x-model"] = "status"
            else:
                field = forms.CharField(max_length=200)
            field.label = pf.label
            field.required = pf.required
            field.help_text = hints.get(pf.key, "")
            if self.status_field and pf.key in CONDITIONAL_KEYS:
                field.required = False  # decided per status in clean()
            field.widget.attrs["class"] = "rs-input"
            self.fields[pf.form_name] = field
        if questionnaire.require_consent:
            self.fields["consent"] = forms.BooleanField(
                required=True,
                label=_("I have read the information above and agree to take part voluntarily."),
                widget=forms.CheckboxInput(attrs={"class": "rs-checkbox"}),
            )

    def role_config(self) -> dict:
        """{status: {form_name: {label, required, hint}}} for the conditional fields of this form."""
        if not self.status_field:
            return {}
        names = {pf.key: pf.form_name for pf in self.field_defs if pf.key in CONDITIONAL_KEYS}
        return {
            status: {
                names[key]: {"label": str(label), "required": required, "hint": str(hint)}
                for key, (label, required, hint) in spec.items()
                if key in names
            }
            for status, spec in ROLE_FIELDS.items()
        }

    def conditional_names(self) -> list[str]:
        if not self.status_field:
            return []
        return [pf.form_name for pf in self.field_defs if pf.key in CONDITIONAL_KEYS]

    def clean(self):
        data = super().clean()
        if not self.status_field:
            return data
        status = data.get(self.status_field.form_name) or ""
        spec = ROLE_FIELDS.get(status, {})
        for pf in self.field_defs:
            if pf.key not in CONDITIONAL_KEYS:
                continue
            if pf.key not in spec:
                data[pf.form_name] = ""  # not applicable to this status: never stored
                continue
            label, required, _hint = spec[pf.key]
            if required and not data.get(pf.form_name):
                self.add_error(pf.form_name, _("“%(f)s” is required.") % {"f": label})
        return data

    def participant_data(self) -> dict:
        data: dict = {}
        for pf in self.field_defs:
            value = self.cleaned_data.get(pf.form_name)
            if value in (None, ""):
                continue
            if pf.key == ParticipantField.Key.CUSTOM:
                data[pf.label] = str(value)
            elif pf.key in Participant.STANDARD_FIELDS:
                data[pf.key] = value
        return data


class ResponseFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(label=_("Search"), required=False)
    questionnaire = forms.ModelChoiceField(label=_("Questionnaire"), queryset=Questionnaire.objects.none(), required=False)
    status = forms.ChoiceField(label=_("Status"), required=False, choices=[("", _("All")), ("completed", _("Completed")), ("in_progress", _("Incomplete"))])
    evaluation = forms.ChoiceField(
        label=_("Evaluation"), required=False,
        choices=[("", _("All")), ("pending", _("Awaiting evaluation")), ("reviewed", _("Evaluated"))],
    )
    date_from = forms.DateField(label=_("From"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label=_("To"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    sort = forms.ChoiceField(
        label=_("Sort"), required=False,
        choices=[("-started_at", _("Newest first")), ("started_at", _("Oldest first")), ("-submitted_at", _("Recently submitted")), ("-evaluation__final_score", _("Highest score")), ("evaluation__final_score", _("Lowest score"))],
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["questionnaire"].queryset = Questionnaire.objects.visible_to(user).order_by("title")
