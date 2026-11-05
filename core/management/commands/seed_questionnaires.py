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
        try:
            self._import_manifest(seed_dir)
        finally:
            # After imports, so criteria created from the manifest get their rules in the same run.
            self._apply_auto_rules(seed_dir)

    def _import_manifest(self, seed_dir: Path) -> None:
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
                self._ensure_criteria(existing, entry, owner)
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
            self._ensure_criteria(qn, entry, owner)
            self._maybe_publish(qn, entry, owner)

    def _apply_auto_rules(self, seed_dir: Path) -> None:
        """Attach automatic scoring rules (seed/auto_rules.json, keyed by criterion name).

        A rule is attached only to criteria without a rule whose questionnaire contains
        every question code the rule refers to, so it never applies to an unrelated survey.
        """
        import re

        from evaluation.autoscore import validate_rule
        from evaluation.models import EvaluationCriterion

        path = seed_dir / "auto_rules.json"
        if not path.exists():
            return
        rules = json.loads(path.read_text(encoding="utf-8"))
        for name, rule in rules.items():
            if not validate_rule(rule):
                self.stderr.write(f"auto rule for “{name}” is invalid — skipped.")
                continue
            codes = set(re.findall(r'"(Q\d+)"', json.dumps(rule)))
            for crit in EvaluationCriterion.objects.filter(name=name).select_related("questionnaire"):
                if crit.auto_rule:
                    continue
                have = set(crit.questionnaire.questions.values_list("code", flat=True))
                if not codes <= have:
                    continue
                crit.auto_rule = rule
                crit.save(update_fields=["auto_rule"])
                self.stdout.write(self.style.SUCCESS(f"auto rule attached: questionnaire #{crit.questionnaire_id} — {name}"))

    def _ensure_criteria(self, qn, entry, owner):
        """Create the manifest's evaluation criteria only if the questionnaire has none yet.

        Criteria the researcher has edited or added in the interface are never overwritten.
        """
        from decimal import Decimal

        from evaluation.models import EvaluationCriterion

        items = entry.get("criteria") or []
        if not items or EvaluationCriterion.objects.filter(questionnaire=qn).exists():
            return
        EvaluationCriterion.objects.bulk_create(
            [
                EvaluationCriterion(
                    questionnaire=qn,
                    name=c["name"][:255],
                    description=c.get("description", ""),
                    max_score=Decimal(str(c.get("max_score", 10))),
                    weight=Decimal(str(c.get("weight", 1))),
                    order=c.get("order", i),
                )
                for i, c in enumerate(items)
            ]
        )
        log_action(None, "evaluation.criteria", qn, user=owner, via="seed_questionnaires", count=len(items))
        self.stdout.write(self.style.SUCCESS(f"{entry['file']}: {len(items)} evaluation criteria created."))

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
