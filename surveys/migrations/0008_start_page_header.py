"""Tidy the start page of the Bokhtar State University questionnaire (requested 2026-11-05).

* Show the university emblem above the title.
* The header names the ministry: "ВАЗОРАТИ МАОРИФ ВА ИЛМИ ҶУМҲУРИИ ТОҶИКИСТОН".
* Published titles lose the internal import label "(версияи ислоҳшуда)".
* The instructions lose the trailing researcher-only line
  "Маълумот дар бораи анкета (аз ҷониби муҳаққиқ пур карда мешавад)".
Runs once; later edits made in the interface are not touched.
"""
from django.db import migrations

UNIVERSITY = "ДОНИШГОҲИ ДАВЛАТИИ БОХТАР"
OLD_FIRST_LINE = "ҶУМҲУРИИ ТОҶИКИСТОН"
NEW_FIRST_LINE = "ВАЗОРАТИ МАОРИФ ВА ИЛМИ ҶУМҲУРИИ ТОҶИКИСТОН"
IMPORT_LABELS = (" (версияи ислоҳшуда)", " (матни аслӣ)")
RESEARCHER_LINE = "Маълумот дар бораи анкета"


def forwards(apps, schema_editor):
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    for qn in Questionnaire.objects.filter(description__icontains=UNIVERSITY):
        lines = qn.description.splitlines()
        if lines and lines[0].strip() == OLD_FIRST_LINE:
            lines[0] = NEW_FIRST_LINE
        qn.description = "\n".join(lines)
        qn.show_institution_emblem = True

        instr = qn.instructions.rstrip().splitlines()
        if instr and instr[-1].strip().startswith(RESEARCHER_LINE):
            instr.pop()
        qn.instructions = "\n".join(instr)

        if qn.status == "published":
            for label in IMPORT_LABELS:
                if qn.title.endswith(label):
                    qn.title = qn.title[: -len(label)]
        qn.save(update_fields=["description", "show_institution_emblem", "instructions", "title"])


class Migration(migrations.Migration):
    dependencies = [("surveys", "0007_questionnaire_emblem")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
