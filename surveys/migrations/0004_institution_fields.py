"""Participant information for schools, colleges and universities (requested 2026-11-05).

1. Field labels saved with the old defaults are renamed (custom labels are kept).
2. On published questionnaires, "Level of education" and "Educational institution"
   are collected (required), and "Faculty / department" and "Specialty / field of
   study" are offered as optional fields, since school pupils have neither.
Runs once; later changes made in the interface are not touched.
"""
from django.db import migrations

NEW = {
    "education_level": "Сатҳи таҳсилот",
    "university": "Муассисаи таҳсилотӣ",
    "faculty": "Факултет / шуъба",
    "specialty": "Ихтисос / самт",
    "academic_year": "Курс ё синф",
}
OLD = {
    "education_level": {"Education level", "Сатҳи таҳсилот", "Уровень образования"},
    "university": {"University", "Донишгоҳ", "Университет"},
    "faculty": {"Faculty", "Факултет", "Факультет"},
    "specialty": {"Specialty", "Ихтисос", "Специальность"},
    "academic_year": {"Academic year", "Курс"},
}
ENABLE = [  # key, required, order
    ("education_level", True, 0),
    ("university", True, 1),
    ("faculty", False, 2),
    ("specialty", False, 3),
]


def forwards(apps, schema_editor):
    Field = apps.get_model("surveys", "ParticipantField")
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    for pf in Field.objects.filter(key__in=NEW.keys()):
        if pf.label in OLD[pf.key] and pf.label != NEW[pf.key]:
            if not Field.objects.filter(questionnaire_id=pf.questionnaire_id, label=NEW[pf.key]).exclude(pk=pf.pk).exists():
                pf.label = NEW[pf.key]
                pf.save(update_fields=["label"])
    for qn in Questionnaire.objects.filter(status="published"):
        for key, required, order in ENABLE:
            pf = Field.objects.filter(questionnaire=qn, key=key).first()
            if pf is None:
                pf = Field(questionnaire=qn, key=key, label=NEW[key])
            pf.enabled = True
            pf.required = required
            pf.order = order
            pf.save()


class Migration(migrations.Migration):
    dependencies = [("surveys", "0003_participant_field_labels")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
