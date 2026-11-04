import csv
import io

import docx
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from reports.models import ExportHistory, ResearchReport
from surveys.tests.helpers import PASSWORD, make_questionnaire, make_user, submit_response, valid_post


@override_settings(LANGUAGE_CODE="en")
class ExportTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.qn = make_questionnaire(self.user)
        self.r1 = submit_response(self.qn, valid_post(self.qn, text="=HYPERLINK(\"http://evil\")"))
        self.r2 = submit_response(self.qn, valid_post(self.qn, gender="Female", text="Ҷавоби тоҷикӣ"))
        self.client.login(username="alice", password=PASSWORD)

    def get(self, kind):
        return self.client.get(reverse("reports:export_dataset", args=[self.qn.pk, kind]))

    def test_excel_export(self):
        r = self.get("xlsx")
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(len(wb.sheetnames), 8)
        ws = wb.worksheets[0]
        self.assertEqual(ws.max_row, 3)  # header + 2 responses
        header = [c.value for c in ws[1]]
        self.assertIn("Q1", header)
        self.assertIn("Q2_1", header)
        values = [c.value for row in ws.iter_rows(min_row=2) for c in row]
        self.assertIn("Ҷавоби тоҷикӣ", values)
        self.assertIn("'=HYPERLINK(\"http://evil\")", values, "formula injection must be neutralised")
        self.assertTrue(ExportHistory.objects.filter(file_format="xlsx", record_count=2).exists())

    def test_csv_export(self):
        r = self.get("csv")
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 3)
        self.assertIn("Female", rows[2])
        coded = list(csv.reader(io.StringIO(self.get("csv-codes").content.decode("utf-8-sig"))))
        q1 = coded[0].index("Q1")
        self.assertEqual({coded[1][q1], coded[2][q1]}, {"1", "2"})

    def test_word_and_pdf_reports(self):
        d = docx.Document(io.BytesIO(self.get("stats-docx").content))
        self.assertIn(self.qn.title, "\n".join(p.text for p in d.paragraphs))
        self.assertTrue(self.get("stats-pdf").content.startswith(b"%PDF"))
        self.assertEqual(self.get("responses-docx").status_code, 200)

    def test_individual_response_exports(self):
        for fmt in ("docx", "pdf", "xlsx"):
            r = self.client.get(reverse("reports:export_response", args=[self.r1.pk, fmt]))
            self.assertEqual(r.status_code, 200, fmt)
        r = self.client.get(reverse("reports:export_selected") + f"?ids={self.r1.pk}&ids={self.r2.pk}")
        self.assertEqual(r.status_code, 200)

    def test_report_generator_flow(self):
        r = self.client.post(reverse("reports:create"), {"questionnaire": self.qn.pk})
        report = ResearchReport.objects.get()
        self.assertRedirects(r, reverse("reports:edit", args=[report.pk]))
        self.client.post(reverse("reports:edit", args=[report.pk]), {
            "title": "Final report", "objectives": "Obj", "methodology": "Survey", "participants_text": "Students",
            "findings": "Finding one", "discussion": "Disc", "conclusions": "Concl", "recommendations": "Rec",
            "include_charts": "on", "include_question_analysis": "on", "status": "final",
        })
        report.refresh_from_db()
        self.assertEqual(report.title, "Final report")
        self.assertContains(self.client.get(reverse("reports:preview", args=[report.pk])), "Finding one")
        out = docx.Document(io.BytesIO(self.client.get(reverse("reports:export", args=[report.pk, "docx"])).content))
        text = "\n".join(p.text for p in out.paragraphs)
        for expected in ("Final report", "Methodology", "Conclusions", "Recommendations", "Concl"):
            self.assertIn(expected, text)
        self.assertTrue(self.client.get(reverse("reports:export", args=[report.pk, "pdf"])).content.startswith(b"%PDF"))

    def test_exports_respect_permissions(self):
        make_user("bob")
        self.client.login(username="bob", password=PASSWORD)
        self.assertEqual(self.get("xlsx").status_code, 404)
        self.assertEqual(self.client.get(reverse("reports:export_selected") + f"?ids={self.r1.pk}").status_code, 404)
        self.client.logout()
        self.assertEqual(self.get("csv").status_code, 302)
