"""The questionnaire is now open to pupils, students and teachers (requested 2026-11-06).

Replaces the audience line at the top of the instructions:
"(барои донишҷӯёни муассисаҳои таҳсилоти олии касбӣ)" →
"(барои хонандагон, донишҷӯён ва омӯзгорони муассисаҳои таҳсилоти умумӣ,
миёнаи касбӣ ва олии касбӣ)". Runs once; later edits are not touched.
"""
from django.db import migrations

OLD = "(барои донишҷӯёни муассисаҳои таҳсилоти олии касбӣ)"
NEW = "(барои хонандагон, донишҷӯён ва омӯзгорони муассисаҳои таҳсилоти умумӣ, миёнаи касбӣ ва олии касбӣ)"


def forwards(apps, schema_editor):
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    for qn in Questionnaire.objects.filter(instructions__contains=OLD):
        qn.instructions = qn.instructions.replace(OLD, NEW)
        qn.save(update_fields=["instructions"])


class Migration(migrations.Migration):
    dependencies = [("surveys", "0008_start_page_header")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
