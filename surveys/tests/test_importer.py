from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from surveys.importer import ImportError_, parse_docx
from surveys.models import Question, Questionnaire

from .helpers import PASSWORD, build_original_style, docx_bytes, make_user


class ParseDocxTests(SimpleTestCase):
    def setUp(self):
        self.parsed = parse_docx(docx_bytes(build_original_style))

    def test_title_and_sections(self):
        self.assertEqual(self.parsed.title, "ИСТИФОДАИ ЗЕҲНИ СУНЪӢ ДАР ОМӮЗИШИ ФАНҲОИ ДАҚИҚ")
        titles = [s.title for s in self.parsed.sections]
        self.assertEqual(titles, ["ҚИСМИ I. МАЪЛУМОТИ УМУМӢ", "A. Дарки фоида", "ҚИСМИ II. САВОЛҲОИ КУШОДА"])

    def test_numbering_and_wording_preserved(self):
        qs = self.parsed.questions
        self.assertEqual([q.number for q in qs], ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual(qs[0].text, "Шакли таҳсил:")
        self.assertEqual(qs[3].text, "Зеҳни сунъӣ ба ман кӯмак мерасонад.")

    def test_question_types(self):
        types = {q.number: q.qtype for q in self.parsed.questions}
        self.assertEqual(types, {"1": "single", "2": "multiple", "3": "yes_no", "4": "likert", "5": "likert", "6": "long_text", "7": "long_text"})

    def test_options_and_other_text(self):
        q1, q2 = self.parsed.questions[:2]
        self.assertEqual([o.text for o in q1.options], ["рӯзона", "ғоибона", "фосилавӣ/омехта"])
        self.assertEqual([o.text for o in q2.options], ["ChatGPT", "Gemini", "дигар"])
        self.assertTrue(q2.options[2].allows_text)
        self.assertFalse(q2.options[0].allows_text)

    def test_likert_legend_labels(self):
        q4 = self.parsed.questions[3]
        self.assertEqual([o.value for o in q4.options], [1, 2, 3, 4, 5])
        self.assertEqual(q4.options[0].text, "комилан розӣ нестам")
        self.assertEqual(q4.options[4].text, "комилан розӣ ҳастам")

    def test_required_flags(self):
        req = {q.number: q.required for q in self.parsed.questions}
        self.assertTrue(req["1"])
        self.assertTrue(req["4"])
        self.assertTrue(req["6"], "unmarked open question is required when the section marks others optional")
        self.assertFalse(req["7"])

    def test_researcher_part_is_not_imported(self):
        self.assertNotIn("99", [q.number for q in self.parsed.questions])
        self.assertTrue(any("researcher-only" in i for i in self.parsed.ignored))

    def test_participant_fields_detected(self):
        self.assertIn("name", self.parsed.participant_fields)
        self.assertIn("academic_year", self.parsed.participant_fields)
        self.assertIn("age", self.parsed.participant_fields)

    def test_options_in_table_cells_and_consent_box(self):
        def build(d):
            d.add_paragraph("☐ I agree to participate.")
            d.add_paragraph("ҚИСМИ I. Demographics")
            d.add_paragraph("1. Course:  (як ҷавоб)")
            t = d.add_table(rows=1, cols=3)
            for i, v in enumerate(["1", "2", "3"]):
                t.cell(0, i).text = f"☐ {v}"
            d.add_paragraph("ЗАМИМА. ДАСТУРИ РАМЗГУЗОРӢ")
            d.add_paragraph("2. Should not be imported")

        parsed = parse_docx(docx_bytes(build))
        self.assertEqual(parsed.consent_text, "I agree to participate.")
        self.assertEqual(len(parsed.questions), 1)
        self.assertEqual([o.text for o in parsed.questions[0].options], ["1", "2", "3"])

    def test_rejects_non_docx(self):
        with self.assertRaises(ImportError_):
            parse_docx(BytesIO(b"plain text, not a docx"))
        with self.assertRaises(ImportError_):
            parse_docx(BytesIO(b"PK\x03\x04broken zip"))


class ImportViewTests(TestCase):
    def setUp(self):
        self.user = make_user("researcher")
        self.client.login(username="researcher", password=PASSWORD)

    def upload(self, content: bytes, name="q.docx"):
        return self.client.post(
            reverse("surveys:import"),
            {"document": SimpleUploadedFile(name, content, content_type="application/octet-stream"), "language": "tg"},
        )

    def test_import_creates_reviewable_draft(self):
        response = self.upload(docx_bytes(build_original_style).getvalue())
        qn = Questionnaire.objects.get()
        self.assertRedirects(response, qn.get_absolute_url())
        self.assertEqual(qn.status, Questionnaire.Status.DRAFT)
        self.assertIsNone(qn.approved_at)
        self.assertEqual(qn.owner, self.user)
        self.assertEqual(qn.questions.count(), 7)
        self.assertEqual(qn.questions.filter(qtype=Question.Type.LIKERT).count(), 2)
        self.assertTrue(qn.source_file.name.endswith(".docx"))
        self.assertNotIn("q.docx", qn.source_file.name, "storage name must be randomised")
        self.assertTrue(qn.participant_fields.filter(key="academic_year", enabled=True).exists())

    def test_rejects_wrong_extension(self):
        response = self.upload(docx_bytes(build_original_style).getvalue(), name="q.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Questionnaire.objects.exists())

    def test_rejects_renamed_text_file(self):
        response = self.upload(b"this is not a word file", name="fake.docx")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rs-error")
        self.assertFalse(Questionnaire.objects.exists())

    def test_requires_login(self):
        self.client.logout()
        response = self.upload(docx_bytes(build_original_style).getvalue())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Questionnaire.objects.exists())


class SchoolGradesMigrationTests(TestCase):
    def test_question_1_gets_grades_and_becomes_optional(self):
        import importlib
        from pathlib import Path

        from django.apps import apps
        from django.conf import settings

        from surveys import services

        user = make_user("owner")
        path = Path(settings.BASE_DIR) / "seed" / "02-pursishnoma-islohshuda.docx"
        with path.open("rb") as fh:
            qn = services.create_from_parsed(parse_docx(fh), user)
        Questionnaire.objects.filter(pk=qn.pk).update(status="published")
        mig = importlib.import_module("surveys.migrations.0006_pupils_and_teachers")
        mig.forwards(apps, None)
        mig.forwards(apps, None)  # idempotent
        q1 = qn.questions.get(code="Q1")
        self.assertEqual([o.text for o in q1.options.all()], ["1", "2", "3", "4", "5", "магистратура", "синфи 5–9", "синфи 10–11"])
        self.assertEqual([o.value for o in q1.options.all()][-2:], [7, 8])
        self.assertFalse(q1.required)
        self.assertFalse(qn.questions.get(code="Q11").required)
        self.assertTrue(qn.questions.get(code="Q2").required)
