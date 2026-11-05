import importlib
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from core.models import User
from evaluation.models import EvaluationCriterion
from surveys.models import Questionnaire
from surveys.tests.helpers import make_questionnaire, make_user, submit_response, valid_post

SEED = Path(settings.BASE_DIR) / "seed"


class SeedTestBase(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        for f in ("01-pursishnoma-asli.docx", "02-pursishnoma-islohshuda.docx", "auto_rules.json"):
            shutil.copy(SEED / f, self.dir / f)
        rules = json.loads((SEED / "auto_rules.json").read_text(encoding="utf-8"))
        criteria = [{"name": n, "max_score": 10, "weight": w, "order": i} for i, (n, w) in enumerate(zip(rules, [1, 2, 3, 2, 2]))]
        manifest = [
            {"file": "01-pursishnoma-asli.docx", "label": "матни аслӣ", "language": "tg"},
            {"file": "02-pursishnoma-islohshuda.docx", "label": "версияи ислоҳшуда", "language": "tg", "publish": True, "criteria": criteria},
        ]
        (self.dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def seed(self):
        out = StringIO()
        call_command("seed_questionnaires", "--dir", str(self.dir), stdout=out)
        return out.getvalue()


class SeedQuestionnairesTests(SeedTestBase):
    def test_imports_once_publishes_corrected_and_attaches_rules(self):
        admin = make_user("admin", role=User.Role.ADMIN)
        self.seed()
        self.assertIn("skipped", self.seed())
        original, corrected = Questionnaire.objects.filter(import_report__has_key="seed_file").order_by("pk")
        self.assertEqual((original.questions.count(), corrected.questions.count()), (40, 53))
        self.assertEqual(original.status, Questionnaire.Status.DRAFT)
        self.assertEqual(corrected.status, Questionnaire.Status.PUBLISHED)
        self.assertEqual(corrected.approved_by, admin)
        crit = EvaluationCriterion.objects.filter(questionnaire=corrected)
        self.assertEqual(crit.count(), 5)
        self.assertTrue(all(c.auto_rule for c in crit), "rules attached to every criterion")
        self.assertFalse(EvaluationCriterion.objects.filter(questionnaire=original).exists())

    def test_publishes_only_once(self):
        make_user("admin", role=User.Role.ADMIN)
        self.seed()
        corrected = Questionnaire.objects.get(import_report__seed_file="02-pursishnoma-islohshuda.docx")
        Questionnaire.objects.filter(pk=corrected.pk).update(status=Questionnaire.Status.DRAFT, approved_at=None)
        self.seed()
        corrected.refresh_from_db()
        self.assertEqual(corrected.status, Questionnaire.Status.DRAFT)

    def test_rules_not_attached_to_unrelated_questionnaire(self):
        user = make_user("admin", role=User.Role.ADMIN)
        other = make_questionnaire(user, title="Unrelated")  # only Q1–Q12
        EvaluationCriterion.objects.create(questionnaire=other, name="Мавқеи интиқодӣ ва масъулиятнок", max_score=10, weight=1)
        self.seed()
        self.assertEqual(EvaluationCriterion.objects.get(questionnaire=other).auto_rule, {})

    def test_skips_without_admin(self):
        self.seed()
        self.assertFalse(Questionnaire.objects.exists())

    def test_repository_manifest_is_valid(self):
        self.assertIsInstance(json.loads((SEED / "manifest.json").read_text(encoding="utf-8")), list)


class EmptyQuestionnaireCleanupTests(TestCase):
    def test_deletes_only_questionnaires_without_participants(self):
        user = make_user("alice")
        empty = make_questionnaire(user, title="Empty")
        used = make_questionnaire(user, title="Used")
        submit_response(used, valid_post(used))
        migration = importlib.import_module("surveys.migrations.0002_delete_empty_questionnaires")
        migration.delete_empty(apps, None)
        self.assertFalse(Questionnaire.objects.filter(pk=empty.pk).exists())
        self.assertTrue(Questionnaire.objects.filter(pk=used.pk).exists())
