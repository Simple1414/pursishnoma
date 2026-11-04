from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from evaluation.models import EvaluationCriterion, EvaluationHistory
from evaluation.services import save_evaluation, weighted_score
from surveys import services as survey_services
from surveys.models import IndividualAnswer
from surveys.tests.helpers import PASSWORD, make_questionnaire, make_user, submit_response, valid_post


class WeightedScoreTests(TestCase):
    def test_weighted_percentage(self):
        qn = make_questionnaire(make_user("w"))
        a = EvaluationCriterion.objects.create(questionnaire=qn, name="A", max_score=Decimal("10"), weight=Decimal("2"))
        b = EvaluationCriterion.objects.create(questionnaire=qn, name="B", max_score=Decimal("5"), weight=Decimal("1"))
        # (8/10*2 + 5/5*1) / 3 * 100 = (1.6 + 1) / 3 * 100 = 86.67
        self.assertEqual(weighted_score({a: Decimal("8"), b: Decimal("5")}), Decimal("86.67"))
        self.assertIsNone(weighted_score({}))


class EvaluationServiceTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.qn = make_questionnaire(self.user)
        self.c1 = EvaluationCriterion.objects.create(questionnaire=self.qn, name="Depth", max_score=10, weight=1)
        self.c2 = EvaluationCriterion.objects.create(questionnaire=self.qn, name="Accuracy", max_score=10, weight=3)
        self.response = submit_response(self.qn, valid_post(self.qn, text="answer"))

    def test_evaluation_never_changes_original_answers(self):
        before = list(IndividualAnswer.objects.filter(response=self.response).values())
        ev = save_evaluation(self.response, self.user, scores={self.c1.pk: Decimal("5"), self.c2.pk: Decimal("10")}, comment="ok", recommendations="", reviewed=True)
        self.assertEqual(list(IndividualAnswer.objects.filter(response=self.response).values()), before)
        # (0.5*1 + 1*3) / 4 = 87.5
        self.assertEqual(ev.final_score, Decimal("87.50"))
        self.assertEqual(ev.status, "reviewed")

    def test_history_records_each_version(self):
        save_evaluation(self.response, self.user, scores={self.c1.pk: Decimal("5")}, comment="v1", recommendations="", reviewed=False)
        ev = save_evaluation(self.response, self.user, scores={self.c1.pk: Decimal("7"), self.c2.pk: Decimal("7")}, comment="v2", recommendations="r", reviewed=True)
        self.assertEqual(ev.version, 2)
        history = EvaluationHistory.objects.filter(evaluation=ev).order_by("version")
        self.assertEqual([h.snapshot["comment"] for h in history], ["v1", "v2"])
        self.assertIsNone(history[0].snapshot["final_score"], "incomplete scoring has no final score")
        self.assertEqual(history[1].snapshot["final_score"], "70.00")

    def test_score_above_maximum_is_rejected(self):
        with self.assertRaises(ValidationError):
            save_evaluation(self.response, self.user, scores={self.c1.pk: Decimal("11")}, comment="", recommendations="", reviewed=False)

    def test_incomplete_response_cannot_be_evaluated(self):
        pending = survey_services.start_response(self.qn, {}, consent=True, ip_hash="", language="en")
        with self.assertRaises(ValidationError):
            save_evaluation(pending, self.user, scores={}, comment="", recommendations="", reviewed=False)


class EvaluationViewTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.qn = make_questionnaire(self.user)
        self.criterion = EvaluationCriterion.objects.create(questionnaire=self.qn, name="Quality", max_score=10, weight=1)
        self.response = submit_response(self.qn, valid_post(self.qn))
        self.client.login(username="alice", password=PASSWORD)

    def test_evaluate_via_form(self):
        url = reverse("evaluation:evaluate", args=[self.response.pk])
        r = self.client.post(url, {f"score_{self.criterion.pk}": "9,5", "comment": "Good", "recommendations": "More detail", "reviewed": "on"})
        self.assertRedirects(r, url)
        ev = self.response.evaluation.__class__.objects.get(response=self.response)
        self.assertEqual(ev.final_score, Decimal("95.00"))
        self.assertEqual(ev.comment, "Good")
        queue = self.client.get(reverse("evaluation:queue") + "?state=reviewed")
        self.assertContains(queue, self.response.reference)

    def test_invalid_score_shows_error(self):
        r = self.client.post(reverse("evaluation:evaluate", args=[self.response.pk]), {f"score_{self.criterion.pk}": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rs-alert-danger")

    def test_define_criteria(self):
        url = reverse("evaluation:criteria", args=[self.qn.pk])
        data = {
            "crit-TOTAL_FORMS": "2", "crit-INITIAL_FORMS": "1", "crit-MIN_NUM_FORMS": "0", "crit-MAX_NUM_FORMS": "1000",
            "crit-0-id": str(self.criterion.pk), "crit-0-name": "Quality", "crit-0-max_score": "10", "crit-0-weight": "2", "crit-0-order": "0",
            "crit-1-name": "Originality", "crit-1-max_score": "5", "crit-1-weight": "1", "crit-1-order": "1",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(EvaluationCriterion.objects.filter(questionnaire=self.qn).count(), 2)
        self.criterion.refresh_from_db()
        self.assertEqual(self.criterion.weight, Decimal("2"))

    def test_other_researcher_cannot_evaluate(self):
        make_user("bob")
        self.client.login(username="bob", password=PASSWORD)
        r = self.client.post(reverse("evaluation:evaluate", args=[self.response.pk]), {f"score_{self.criterion.pk}": "1"})
        self.assertEqual(r.status_code, 404)
