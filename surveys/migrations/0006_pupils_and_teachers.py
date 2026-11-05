"""Open the published questionnaire to school pupils and teachers (requested 2026-11-05).

* "Level of education" becomes "Your status" (pupil / student / teacher …).
* Published questionnaires also offer "Year or grade" and "Subject taught"
  (shown only for the statuses they apply to).
* Question 1 "Курси таҳсил" gets the options "синфи 5–9" and "синфи 10–11"
  and is no longer required (teachers have no year of study). Question 11
  (results of the last semester) is no longer required either, for the same reason.
Runs once; later edits made in the interface are not touched.
"""
from django.db import migrations
from django.db.models import Max


def forwards(apps, schema_editor):
    Field = apps.get_model("surveys", "ParticipantField")
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    Question = apps.get_model("surveys", "Question")
    Option = apps.get_model("surveys", "AnswerOption")

    for pf in Field.objects.filter(key="education_level", label__in=["Сатҳи таҳсилот", "Education level", "Level of education", "Уровень образования"]):
        if not Field.objects.filter(questionnaire_id=pf.questionnaire_id, label="Мақоми шумо").exists():
            pf.label = "Мақоми шумо"
            pf.save(update_fields=["label"])

    for qn in Questionnaire.objects.filter(status="published"):
        for key, label, order in (("academic_year", "Курс ё синф", 4), ("subject", "Фанни таълимӣ", 5)):
            pf = Field.objects.filter(questionnaire=qn, key=key).first()
            if pf is None:
                if Field.objects.filter(questionnaire=qn, label=label).exists():
                    continue
                pf = Field(questionnaire=qn, key=key, label=label)
            pf.enabled = True
            pf.required = False
            pf.order = order
            pf.save()

    for q in Question.objects.filter(code="Q1", text__startswith="Курси таҳсил"):
        opts = Option.objects.filter(question=q, kind="option")
        if not opts.filter(text="магистратура").exists():
            continue  # not the BSU questionnaire layout
        top = opts.aggregate(v=Max("value"), o=Max("order"))
        value, order = (top["v"] or 0), (top["o"] or 0)
        for text in ("синфи 5–9", "синфи 10–11"):
            if not opts.filter(text=text).exists():
                value += 1
                order += 1
                Option.objects.create(question=q, kind="option", text=text, value=value, order=order)
        if q.required:
            q.required = False
            q.save(update_fields=["required"])

    Question.objects.filter(code="Q11", text__startswith="Натиҷаҳои таҳсили Шумо дар семестри охир").update(required=False)


class Migration(migrations.Migration):
    dependencies = [("surveys", "0005_participant_subject")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
