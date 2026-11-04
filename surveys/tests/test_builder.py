from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog, User
from surveys.models import Question, Questionnaire

from .helpers import PASSWORD, make_questionnaire, make_user, submit_response, valid_post


class BuilderTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.client.login(username="alice", password=PASSWORD)

    def test_create_questionnaire_with_default_section(self):
        r = self.client.post(reverse("surveys:create"), {"title": "My study", "language": "tg", "estimated_minutes": 10, "require_consent": "on"})
        qn = Questionnaire.objects.get()
        self.assertRedirects(r, qn.get_absolute_url())
        self.assertEqual(qn.sections.count(), 1)
        self.assertEqual(qn.owner, self.user)
        self.assertEqual(qn.participant_fields.count(), 9)
        self.assertFalse(qn.participant_fields.filter(enabled=True).exists(), "participation is anonymous by default")

    def test_add_question_with_options(self):
        qn = make_questionnaire(self.user, published=False)
        section = qn.sections.first()
        data = {
            "section": section.pk, "number": "50", "code": "Q50", "text": "New question?", "help_text": "",
            "qtype": "single", "required": "on", "scale_min": "", "scale_max": "", "scale_min_label": "", "scale_max_label": "",
            "opt-TOTAL_FORMS": "2", "opt-INITIAL_FORMS": "0", "opt-MIN_NUM_FORMS": "0", "opt-MAX_NUM_FORMS": "1000",
            "opt-0-kind": "option", "opt-0-text": "Yes", "opt-0-value": "1", "opt-0-order": "0",
            "opt-1-kind": "option", "opt-1-text": "No", "opt-1-value": "0", "opt-1-order": "1",
        }
        r = self.client.post(reverse("surveys:question_add", args=[qn.pk]), data)
        self.assertEqual(r.status_code, 302)
        q = Question.objects.get(code="Q50")
        self.assertEqual([o.text for o in q.options.all()], ["Yes", "No"])

    def test_single_choice_needs_two_options(self):
        qn = make_questionnaire(self.user, published=False)
        data = {
            "section": qn.sections.first().pk, "number": "51", "code": "Q51", "text": "?", "qtype": "single",
            "opt-TOTAL_FORMS": "0", "opt-INITIAL_FORMS": "0", "opt-MIN_NUM_FORMS": "0", "opt-MAX_NUM_FORMS": "1000",
        }
        self.client.post(reverse("surveys:question_add", args=[qn.pk]), data)
        self.assertFalse(Question.objects.filter(code="Q51").exists())

    def test_approval_then_publication(self):
        qn = make_questionnaire(self.user, published=False)
        r = self.client.post(reverse("surveys:status", args=[qn.pk, "publish"]))
        qn.refresh_from_db()
        self.assertEqual(qn.status, Questionnaire.Status.DRAFT, "cannot publish before approval")
        self.client.post(reverse("surveys:status", args=[qn.pk, "approve"]))
        self.client.post(reverse("surveys:status", args=[qn.pk, "publish"]))
        qn.refresh_from_db()
        self.assertEqual(qn.status, Questionnaire.Status.PUBLISHED)
        self.assertEqual(qn.approved_by, self.user)
        self.assertTrue(AuditLog.objects.filter(action="questionnaire.publish").exists())
        self.assertEqual(r.status_code, 302)

    def test_state_changes_require_post(self):
        qn = make_questionnaire(self.user, published=False)
        for url in (
            reverse("surveys:status", args=[qn.pk, "approve"]),
            reverse("surveys:duplicate", args=[qn.pk]),
            reverse("surveys:question_delete", args=[qn.pk, qn.questions.first().pk]),
            reverse("surveys:section_add", args=[qn.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_duplicate_creates_independent_draft(self):
        qn = make_questionnaire(self.user)
        self.client.post(reverse("surveys:duplicate", args=[qn.pk]))
        copy = Questionnaire.objects.exclude(pk=qn.pk).get()
        self.assertEqual(copy.status, Questionnaire.Status.DRAFT)
        self.assertNotEqual(copy.public_id, qn.public_id)
        self.assertEqual(copy.questions.count(), qn.questions.count())
        self.assertEqual(sum(q.options.count() for q in copy.questions.all()), sum(q.options.count() for q in qn.questions.all()))

    def test_questionnaire_with_responses_cannot_be_deleted(self):
        qn = make_questionnaire(self.user)
        submit_response(qn, valid_post(qn))
        self.client.post(reverse("surveys:delete", args=[qn.pk]))
        self.assertTrue(Questionnaire.objects.filter(pk=qn.pk).exists())
        r = self.client.post(reverse("surveys:question_delete", args=[qn.pk, qn.questions.first().pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(qn.questions.count(), 12)

    def test_empty_questionnaire_can_be_deleted(self):
        qn = make_questionnaire(self.user, published=False)
        self.client.post(reverse("surveys:delete", args=[qn.pk]))
        self.assertFalse(Questionnaire.objects.filter(pk=qn.pk).exists())

    def test_qr_and_preview(self):
        qn = make_questionnaire(self.user)
        r = self.client.get(reverse("surveys:qr", args=[qn.pk]))
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertEqual(self.client.get(reverse("surveys:preview", args=[qn.pk])).status_code, 200)


class AccessControlTests(TestCase):
    """Role × view matrix and object-level (IDOR) protection."""

    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.admin = make_user("admin", role=User.Role.ADMIN)
        self.qn = make_questionnaire(self.alice)
        self.resp = submit_response(self.qn, valid_post(self.qn))

    def urls(self):
        qn, r = self.qn, self.resp
        return [
            reverse("surveys:detail", args=[qn.pk]),
            reverse("surveys:edit", args=[qn.pk]),
            reverse("surveys:qr", args=[qn.pk]),
            reverse("surveys:participant_fields", args=[qn.pk]),
            reverse("surveys:response_detail", args=[r.pk]),
            reverse("evaluation:evaluate", args=[r.pk]),
            reverse("evaluation:criteria", args=[qn.pk]),
            reverse("analytics:overview", args=[qn.pk]),
            reverse("analytics:intelligence", args=[qn.pk]),
            reverse("reports:export_dataset", args=[qn.pk, "xlsx"]),
            reverse("reports:export_response", args=[r.pk, "pdf"]),
        ]

    def test_owner_has_access(self):
        self.client.login(username="alice", password=PASSWORD)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_other_researcher_gets_404(self):
        self.client.login(username="bob", password=PASSWORD)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_admin_has_access(self):
        self.client.login(username="admin", password=PASSWORD)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_anonymous_is_redirected_to_login(self):
        for url in self.urls():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn(reverse("login"), r["Location"])

    def test_inactive_user_is_denied(self):
        User.objects.filter(username="alice").update(is_active=False)
        self.assertFalse(self.client.login(username="alice", password=PASSWORD))

    def test_lists_only_show_own_data(self):
        make_questionnaire(self.bob, title="Bob study")
        self.client.login(username="alice", password=PASSWORD)
        r = self.client.get(reverse("surveys:list"))
        self.assertContains(r, self.qn.title)
        self.assertNotContains(r, "Bob study")
        self.client.login(username="bob", password=PASSWORD)
        r = self.client.get(reverse("surveys:responses"))
        self.assertNotContains(r, self.resp.reference)

    def test_bob_cannot_change_alices_questionnaire(self):
        self.client.login(username="bob", password=PASSWORD)
        r = self.client.post(reverse("surveys:status", args=[self.qn.pk, "close"]))
        self.assertEqual(r.status_code, 404)
        self.qn.refresh_from_db()
        self.assertEqual(self.qn.status, Questionnaire.Status.PUBLISHED)

    def test_api_requires_staff_and_filters_objects(self):
        url = reverse("questionnaire-list")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.login(username="bob", password=PASSWORD)
        self.assertEqual(self.client.get(url).json()["count"], 0)
        self.client.login(username="alice", password=PASSWORD)
        data = self.client.get(url).json()
        self.assertEqual(data["count"], 1)
        stats = self.client.get(reverse("questionnaire-statistics", args=[self.qn.pk])).json()
        self.assertEqual(stats["N"], 1)
