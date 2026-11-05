"""Import the questionnaires listed in seed/manifest.json (idempotent).

Each .docx is parsed with the regular Word importer and saved as a DRAFT
owned by the administrator, so it still has to be reviewed, approved and
published in the Survey Builder. A file that was already imported is
skipped, so the command is safe to run on every start.
"""
import json
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand

from surveys.importer import parse_docx
from surveys.models import Questionnaire
from surveys.services import create_from_parsed


class Command(BaseCommand):
    help = "Import seed questionnaires from seed/manifest.json as drafts (skips ones already imported)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=str(Path(settings.BASE_DIR) / "seed"))

    def handle(self, *args, **options):
        seed_dir = Path(options["dir"])
        manifest = seed_dir / "manifest.json"
        if not manifest.exists():
            self.stdout.write("No seed manifest — nothing to import.")
            return
        owner = self._owner()
        if owner is None:
            self.stdout.write("No administrator account yet — seed questionnaires skipped.")
            return
        for entry in json.loads(manifest.read_text(encoding="utf-8")):
            name = entry["file"]
            if Questionnaire.objects.filter(import_report__seed_file=name).exists():
                self.stdout.write(f"{name}: already imported — skipped.")
                continue
            path = seed_dir / name
            with path.open("rb") as fh:
                parsed = parse_docx(fh, fallback_title=path.stem)
                if entry.get("label"):
                    parsed.title = f"{parsed.title} ({entry['label']})"
                fh.seek(0)
                qn = create_from_parsed(parsed, owner, entry.get("language", "tg"), source_file=File(fh, name=name))
            qn.import_report = {**qn.import_report, "seed_file": name}
            qn.save(update_fields=["import_report"])
            self.stdout.write(
                self.style.SUCCESS(f"{name}: imported as draft #{qn.pk} — {len(parsed.questions)} questions, {len(parsed.sections)} sections.")
            )

    @staticmethod
    def _owner():
        User = get_user_model()
        username = os.getenv("DJANGO_ADMIN_USERNAME", "").strip()
        if username:
            user = User.objects.filter(username=username, is_active=True).first()
            if user:
                return user
        return User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
