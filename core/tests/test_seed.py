import os
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from surveys.models import Questionnaire
from surveys.tests.helpers import make_user
from core.models import User


class SeedQuestionnairesTests(TestCase):
    def test_imports_seed_files_once_as_drafts(self):
        admin = make_user("admin", role=User.Role.ADMIN)
        os.environ["DJANGO_ADMIN_USERNAME"] = "admin"
        try:
            call_command("seed_questionnaires", stdout=StringIO())
            out = StringIO()
            call_command("seed_questionnaires", stdout=out)
        finally:
            os.environ.pop("DJANGO_ADMIN_USERNAME")
        seeded = Questionnaire.objects.filter(import_report__has_key="seed_file").order_by("pk")
        self.assertEqual(seeded.count(), 2)
        self.assertIn("skipped", out.getvalue())
        self.assertEqual([q.questions.count() for q in seeded], [40, 53])
        original, corrected = seeded
        self.assertEqual(original.status, Questionnaire.Status.DRAFT)
        self.assertEqual(corrected.status, Questionnaire.Status.PUBLISHED)
        self.assertEqual(corrected.approved_by, admin)
        self.assertTrue(corrected.accepting_responses())
        for qn in seeded:
            self.assertEqual(qn.owner, admin)

    def test_publishes_only_once(self):
        make_user("admin", role=User.Role.ADMIN)
        call_command("seed_questionnaires", stdout=StringIO())
        corrected = Questionnaire.objects.get(import_report__seed_file="02-pursishnoma-islohshuda.docx")
        Questionnaire.objects.filter(pk=corrected.pk).update(status=Questionnaire.Status.DRAFT, approved_at=None)
        call_command("seed_questionnaires", stdout=StringIO())
        corrected.refresh_from_db()
        self.assertEqual(corrected.status, Questionnaire.Status.DRAFT, "researcher's later change is respected")

    def test_skips_without_admin(self):
        out = StringIO()
        call_command("seed_questionnaires", stdout=out)
        self.assertFalse(Questionnaire.objects.exists())
