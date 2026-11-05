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
from surveys.services import approve, create_from_parsed, publish

from core.audit import log_action


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
            existing = Questionnaire.objects.filter(import_report__seed_file=name).first()
            if existing is not None:
                self.stdout.write(f"{name}: already imported — skipped.")
                self._maybe_publish(existing, entry, owner)
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
            self._maybe_publish(qn, entry, owner)

    def _maybe_publish(self, qn, entry, owner):
        """Approve and publish once, when the manifest says so and the owner authorised it.

        Runs only for a draft without responses and only the first time, so a
        questionnaire the researcher later closes or returns to draft stays as they left it.
        """
        if not entry.get("publish") or qn.import_report.get("seed_published"):
            return
        if qn.status != Questionnaire.Status.DRAFT or qn.responses.exists():
            return
        approve(qn, owner)
        publish(qn)
        qn.import_report = {**qn.import_report, "seed_published": True}
        qn.save(update_fields=["import_report"])
        log_action(None, "questionnaire.publish", qn, user=owner, via="seed_questionnaires")
        self.stdout.write(self.style.SUCCESS(f"{entry['file']}: approved and published — public URL /s/{qn.public_id}/"))

    @staticmethod
    def _owner():
        User = get_user_model()
        username = os.getenv("DJANGO_ADMIN_USERNAME", "").strip()
        if username:
            user = User.objects.filter(username=username, is_active=True).first()
            if user:
                return user
        from django.db.models import Q

        return (
            User.objects.filter(Q(is_superuser=True) | Q(role=User.Role.ADMIN), is_active=True)
            .order_by("-is_superuser", "pk")
            .first()
        )
