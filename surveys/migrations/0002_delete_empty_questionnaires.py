"""One-time clean-up requested by the platform owner (2026-11-05).

Deletes questionnaires that have no participants at all (no started or
completed responses). Questionnaires with any participant are kept, and
because this is a migration it runs exactly once per database — drafts
created later are never touched. Not reversible: deleted drafts can be
re-imported from their Word files.
"""
from django.db import migrations


def delete_empty(apps, schema_editor):
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    Participant = apps.get_model("surveys", "Participant")
    AuditLog = apps.get_model("core", "AuditLog")
    used = set(Participant.objects.values_list("questionnaire_id", flat=True))
    for qn in Questionnaire.objects.exclude(pk__in=used):
        AuditLog.objects.create(
            action="questionnaire.delete",
            object_type="surveys.Questionnaire",
            object_id=str(qn.pk),
            details={"title": qn.title, "reason": "empty questionnaire clean-up (migration)"},
        )
        qn.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("surveys", "0001_initial"),
        ("core", "0003_tagline_optional"),
        ("evaluation", "0002_automatic_evaluation"),
        ("analytics", "0002_initial"),
        ("reports", "0001_initial"),
    ]
    operations = [migrations.RunPython(delete_empty, migrations.RunPython.noop)]
