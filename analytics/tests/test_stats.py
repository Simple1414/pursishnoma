from unittest import mock

import numpy as np
from django.test import TestCase, override_settings
from django.urls import reverse
from scipy import stats as sps

from analytics import ai, intelligence, stats, text
from analytics.models import AIAnalysis, ResearchFinding
from surveys.tests.helpers import PASSWORD, make_questionnaire, make_user, question, submit_response, valid_post


class DescribeTests(TestCase):
    def test_matches_numpy(self):
        values = [1, 2, 2, 3, 4, 5, 5, 5]
        d = stats.describe(values)
        self.assertEqual(d["n"], 8)
        self.assertAlmostEqual(d["mean"], np.mean(values), places=3)
        self.assertAlmostEqual(d["median"], np.median(values), places=3)
        self.assertAlmostEqual(d["std"], np.std(values, ddof=1), places=3)
        self.assertAlmostEqual(d["variance"], np.var(values, ddof=1), places=3)
        self.assertEqual(d["mode"], [5.0])
        self.assertEqual((d["min"], d["max"]), (1.0, 5.0))

    def test_empty_and_single(self):
        self.assertEqual(stats.describe([]), {"n": 0})
        self.assertIsNone(stats.describe([3])["std"])


@override_settings(LANGUAGE_CODE="en")
class DatasetStatisticsTests(TestCase):
    """Statistics computed from real stored responses."""

    def setUp(self):
        self.user = make_user("alice")
        self.qn = make_questionnaire(self.user)
        self.likert_a = [5, 4, 4, 5, 3, 4, 5, 5, 4, 3, 2, 1]
        genders = ["Male"] * 6 + ["Female"] * 6
        for i, (g, v) in enumerate(zip(genders, self.likert_a)):
            submit_response(self.qn, valid_post(self.qn, gender=g, likert=(v, 3), rating=i % 10 + 1, text="Internet is slow but AI is useful" if i % 2 else "Very useful tool for practice"),
                            faculty="Math" if i < 6 else "Physics", year=str(1 + i % 2))
        self.ds = stats.load_dataset(self.qn)

    def test_frequencies_and_percentages(self):
        s = stats.question_summary(self.ds, question(self.qn, 1))
        self.assertEqual((s["N"], s["n"], s["missing"]), (12, 12, 0))
        self.assertEqual({r["option"]: (r["count"], r["pct"]) for r in s["rows"]}, {"Male": (6, 50.0), "Female": (6, 50.0)})

    def test_likert_statistics(self):
        s = stats.question_summary(self.ds, question(self.qn, 4))
        self.assertAlmostEqual(s["stats"]["mean"], round(np.mean(self.likert_a), 3))
        self.assertAlmostEqual(s["stats"]["std"], round(np.std(self.likert_a, ddof=1), 3))
        self.assertEqual(sum(r["count"] for r in s["rows"]), 12)

    def test_missing_data_counted(self):
        s = stats.question_summary(self.ds, question(self.qn, 7))  # optional numeric, never answered
        self.assertEqual((s["n"], s["missing"], s["missing_pct"]), (0, 12, 100.0))

    def test_crosstab_matches_scipy(self):
        g = f"q:{question(self.qn, 1).pk}"
        res = stats.crosstab(self.ds, g, "p:faculty")
        self.assertEqual(res["n"], 12)
        observed = np.array(res["counts"])
        chi2, p, dof, _e = sps.chi2_contingency(observed, correction=True)
        self.assertAlmostEqual(res["test"]["p"], round(p, 4))
        self.assertTrue(res["test"]["performed"])
        self.assertIn("fisher_p", res["test"])

    def test_group_comparison_uses_mann_whitney(self):
        res = stats.compare_groups(self.ds, f"q:{question(self.qn, 4).pk}", "p:faculty")
        self.assertTrue(res["test"]["performed"])
        u, p = sps.mannwhitneyu(self.likert_a[:6], self.likert_a[6:], alternative="two-sided")
        self.assertAlmostEqual(res["test"]["p"], round(p, 4))
        self.assertEqual([g["n"] for g in res["groups"]], [6, 6])

    def test_small_groups_are_not_tested(self):
        res = stats.compare_groups(self.ds, f"q:{question(self.qn, 4).pk}", f"q:{question(self.qn, 4).pk}")
        self.assertFalse(res["test"]["performed"])
        self.assertIn("5", res["test"]["reason"])

    def test_correlation_spearman(self):
        res = stats.correlation(self.ds, f"q:{question(self.qn, 4).pk}", f"q:{question(self.qn, 6).pk}")
        self.assertTrue(res["test"]["performed"])
        self.assertEqual(res["n"], 12)

    def test_section_mean(self):
        rows = stats.section_comparison(self.ds)["rows"]
        self.assertEqual(len(rows), 1)
        expected = np.mean([(a + 3) / 2 for a in self.likert_a])
        self.assertAlmostEqual(rows[0]["mean"], round(expected, 3))

    def test_findings_are_traceable(self):
        n = intelligence.regenerate_findings(self.qn, self.user)
        self.assertGreater(n, 0)
        for f in ResearchFinding.objects.filter(questionnaire=self.qn):
            self.assertEqual(f.status, "draft")
            self.assertTrue(f.evidence, f.title)

    def test_views_render_real_data(self):
        self.client.login(username="alice", password=PASSWORD)
        r = self.client.get(reverse("analytics:overview", args=[self.qn.pk]))
        self.assertContains(r, "N = 12")
        r = self.client.get(reverse("analytics:crosstab", args=[self.qn.pk]) + f"?row=q:{question(self.qn, 1).pk}&col=p:faculty")
        self.assertContains(r, "chi-square")
        r = self.client.get(reverse("analytics:compare", args=[self.qn.pk]) + f"?value=q:{question(self.qn, 4).pk}&group=p:faculty")
        self.assertContains(r, "Mann")
        self.assertEqual(self.client.get(reverse("analytics:intelligence", args=[self.qn.pk])).status_code, 200)


class EmptyDatasetTests(TestCase):
    def test_no_fake_numbers_without_responses(self):
        user = make_user("alice")
        qn = make_questionnaire(user)
        self.client.login(username="alice", password=PASSWORD)
        r = self.client.get(reverse("analytics:overview", args=[qn.pk]))
        self.assertContains(r, "rs-empty")
        self.assertEqual(intelligence.regenerate_findings(qn, user), 0)


class TextAnalysisTests(TestCase):
    def test_terms_link_to_responses(self):
        docs = [(1, "Интернет суст аст"), (2, "Интернет хеле суст"), (3, "Барнома муфид аст")]
        terms = {t["term"]: t["responses"] for t in text.top_terms(docs)}
        self.assertEqual(terms["интернет"], [1, 2])
        tendencies = text.tendencies(docs)["counts"]
        self.assertEqual(tendencies["negative"], 2)
        self.assertEqual(tendencies["positive"], 1)
        groups = text.similar_groups(docs)
        self.assertEqual(sorted(i["response_id"] for i in groups[0]["items"]), [1, 2])


class AIAssistantTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.qn = make_questionnaire(self.user)
        p = submit_response(self.qn, valid_post(self.qn, text="Secret-free answer"), faculty="Math")
        p.participant.name = "Private Person"
        p.participant.save()

    @override_settings(AI_PROVIDER="none", AI_API_KEY="")
    def test_unconfigured_ai_degrades_gracefully(self):
        analysis = ai.run_analysis(self.qn, AIAnalysis.Task.EXPLAIN, self.user)
        self.assertTrue(analysis.error)
        self.assertEqual(analysis.output_text, "")
        self.client.login(username="alice", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("analytics:assistant", args=[self.qn.pk])).status_code, 200)

    @override_settings(AI_PROVIDER="anthropic", AI_API_KEY="test-key", AI_MODEL="claude-opus-5-5")
    def test_ai_output_is_stored_as_draft_and_context_has_no_names(self):
        fake = mock.MagicMock()
        fake.stop_reason = "end_turn"
        fake.content = [mock.MagicMock(type="text", text="Interpretation based on N = 1.")]
        with mock.patch("anthropic.Anthropic") as client_cls:
            client_cls.return_value.beta.messages.create.return_value = fake
            analysis = ai.run_analysis(self.qn, AIAnalysis.Task.SUMMARIZE, self.user, language="en")
            kwargs = client_cls.return_value.beta.messages.create.call_args.kwargs
        self.assertEqual(analysis.output_text, "Interpretation based on N = 1.")
        self.assertEqual(analysis.status, "draft")
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        sent = kwargs["messages"][0]["content"]
        self.assertNotIn("Private Person", sent)
        self.assertIn("Secret-free answer", sent)
        self.assertIn("R-", sent)
        ai.review(analysis, self.user, approve=True, edited_text="Researcher-approved text")
        analysis.refresh_from_db()
        self.assertEqual((analysis.status, analysis.final_text), ("approved", "Researcher-approved text"))
