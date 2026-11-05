import datetime as dt

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from surveys.models import IndividualAnswer, ImmutableAnswerError, Participant, Questionnaire, SurveyResponse

from .helpers import make_questionnaire, make_user, option, question, valid_post


class PublicFlowTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.qn = make_questionnaire(self.owner)
        self.landing = reverse("public:landing", args=[self.qn.public_id])
        self.start = reverse("public:start", args=[self.qn.public_id])
        self.submit = reverse("public:submit", args=[self.qn.public_id])
        self.save = reverse("public:autosave", args=[self.qn.public_id])

    def begin(self, client=None, **extra):
        client = client or self.client
        return client.post(self.start, {"consent": "on", **extra})

    def test_landing_shows_purpose_and_consent(self):
        r = self.client.get(self.landing)
        self.assertContains(r, self.qn.title)
        self.assertContains(r, "Consent")

    def test_draft_questionnaire_is_not_public(self):
        draft = make_questionnaire(self.owner, published=False, title="Draft")
        self.assertEqual(self.client.get(reverse("public:landing", args=[draft.public_id])).status_code, 404)

    def test_start_requires_consent(self):
        r = self.client.post(self.start, {})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SurveyResponse.objects.exists())

    def test_start_is_post_only(self):
        self.assertEqual(self.client.get(self.start).status_code, 405)
        self.assertEqual(self.client.get(self.submit).status_code, 405)

    def test_anonymous_participation_and_configured_fields(self):
        field = self.qn.participant_fields.get(key="faculty")
        field.enabled = True
        field.save()
        self.begin(**{field.form_name: "Mathematics"})
        participant = Participant.objects.get()
        self.assertEqual(participant.faculty, "Mathematics")
        self.assertEqual(participant.name, "")
        self.assertTrue(participant.consent_given)
        self.assertNotEqual(participant.ip_hash, "127.0.0.1")

    def test_full_submission_is_stored(self):
        self.begin()
        self.assertEqual(self.client.get(reverse("public:fill", args=[self.qn.public_id])).status_code, 200)
        data = valid_post(self.qn, text="Useful for practice", multiple=("A", "Other"))
        data[f"q_{question(self.qn, 2).pk}_other_{option(self.qn, 2, 'Other').pk}"] = "Wolfram"
        q10 = question(self.qn, 10)
        rows, cols = q10.matrix_rows(), q10.matrix_columns()
        data[f"q_{q10.pk}_r_{rows[0].pk}"] = str(cols[1].pk)
        q11 = question(self.qn, 11)
        for rank, o in enumerate(q11.choice_options(), start=1):
            data[f"q_{q11.pk}_rank_{o.pk}"] = str(rank)
        data[f"q_{question(self.qn, 7).pk}"] = "21,5"
        data[f"q_{question(self.qn, 8).pk}"] = "2026-10-01"
        r = self.client.post(self.submit, data)
        self.assertRedirects(r, reverse("public:done", args=[self.qn.public_id]))
        resp = SurveyResponse.objects.get()
        self.assertEqual(resp.status, SurveyResponse.Status.COMPLETED)
        self.assertIsNotNone(resp.submitted_at)
        answers = IndividualAnswer.objects.filter(response=resp)
        self.assertEqual(answers.filter(question=question(self.qn, 2)).count(), 2)
        self.assertEqual(answers.get(question=question(self.qn, 2), option__text="Other").text_value, "Wolfram")
        self.assertEqual(float(answers.get(question=question(self.qn, 7)).number_value), 21.5)
        self.assertEqual(answers.get(question=question(self.qn, 8)).date_value, dt.date(2026, 10, 1))
        self.assertEqual(answers.filter(question=q11).count(), 3)
        self.assertEqual(answers.get(question=q10).option, cols[1])
        done = self.client.get(reverse("public:done", args=[self.qn.public_id]))
        self.assertContains(done, resp.reference)

    def test_required_questions_are_enforced_and_partial_answers_kept(self):
        self.begin()
        partial = {f"q_{question(self.qn, 1).pk}": str(option(self.qn, 1, "Female").pk)}
        r = self.client.post(self.submit, partial)
        self.assertEqual(r.status_code, 400)
        resp = SurveyResponse.objects.get()
        self.assertEqual(resp.status, SurveyResponse.Status.IN_PROGRESS)
        self.assertTrue(IndividualAnswer.objects.filter(response=resp, question=question(self.qn, 1)).exists())

    def test_autosave_persists_progress(self):
        self.begin()
        r = self.client.post(self.save, {f"q_{question(self.qn, 4).pk}": str(option(self.qn, 4, "3").pk)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(IndividualAnswer.objects.get().option.value, 3)
        # Re-opening the page restores the saved answer from the database
        page = self.client.get(reverse("public:fill", args=[self.qn.public_id]))
        self.assertContains(page, f'value="{option(self.qn, 4, "3").pk}" class="sr-only" checked')

    def test_invalid_option_is_rejected(self):
        self.begin()
        other_q_option = option(self.qn, 3, "Yes")
        data = valid_post(self.qn)
        data[f"q_{question(self.qn, 1).pk}"] = str(other_q_option.pk)
        r = self.client.post(self.submit, data)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SurveyResponse.objects.filter(status="completed").exists())

    def test_rating_out_of_range_is_rejected(self):
        self.begin()
        data = valid_post(self.qn, rating=11)
        self.assertEqual(self.client.post(self.submit, data).status_code, 400)

    def test_duplicate_submission_is_idempotent(self):
        self.begin()
        data = valid_post(self.qn)
        self.client.post(self.submit, data)
        first = list(IndividualAnswer.objects.values_list("pk", flat=True))
        data[f"q_{question(self.qn, 1).pk}"] = str(option(self.qn, 1, "Female").pk)
        r = self.client.post(self.submit, data)
        self.assertRedirects(r, reverse("public:done", args=[self.qn.public_id]))
        self.assertEqual(list(IndividualAnswer.objects.values_list("pk", flat=True)), first)
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_completed_answers_are_immutable(self):
        self.begin()
        self.client.post(self.submit, valid_post(self.qn))
        answer = IndividualAnswer.objects.first()
        answer.text_value = "tampered"
        with self.assertRaises(ImmutableAnswerError):
            answer.save()
        with self.assertRaises(ImmutableAnswerError):
            answer.delete()
        # autosave cannot modify a completed response either
        r = self.client.post(self.save, {f"q_{question(self.qn, 1).pk}": str(option(self.qn, 1, "Female").pk)})
        self.assertEqual(r.status_code, 409)

    def test_participant_cannot_open_another_participants_response(self):
        self.begin()
        self.client.post(self.submit, valid_post(self.qn))
        other = self.client_class()
        r = other.get(reverse("public:done", args=[self.qn.public_id]))
        self.assertRedirects(r, self.landing)
        # Staff-only response pages are not reachable for participants
        resp = SurveyResponse.objects.get()
        r = other.get(reverse("surveys:response_detail", args=[resp.pk]))
        self.assertEqual(r.status_code, 302)

    def test_closed_or_expired_questionnaire_rejects_responses(self):
        self.begin()
        Questionnaire.objects.filter(pk=self.qn.pk).update(closes_at=timezone.now() - dt.timedelta(minutes=1), opens_at=timezone.now() - dt.timedelta(days=1))
        r = self.client.post(self.submit, valid_post(self.qn))
        self.assertRedirects(r, self.landing)
        self.assertFalse(SurveyResponse.objects.filter(status="completed").exists())
        fresh = self.client_class()
        self.assertEqual(fresh.post(self.start, {"consent": "on"}).status_code, 302)
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_inactive_questionnaire_rejects_start(self):
        Questionnaire.objects.filter(pk=self.qn.pk).update(is_active=False)
        self.begin()
        self.assertFalse(SurveyResponse.objects.exists())

    @override_settings(RATE_LIMIT_START="2/600")
    def test_start_is_rate_limited(self):
        for _ in range(4):
            self.client_class().post(self.start, {"consent": "on"})
        self.assertEqual(SurveyResponse.objects.count(), 2)


class InstitutionFieldsTests(TestCase):
    """Schools, colleges and universities can all describe themselves."""

    def setUp(self):
        self.qn = make_questionnaire(make_user("owner"))
        import importlib

        from django.apps import apps

        importlib.import_module("surveys.migrations.0004_institution_fields").forwards(apps, None)

    def test_published_questionnaire_collects_institution_details(self):
        fields = {f.key: f for f in self.qn.participant_fields.filter(enabled=True)}
        self.assertEqual(set(fields), {"education_level", "university", "faculty", "specialty"})
        self.assertTrue(fields["education_level"].required and fields["university"].required)
        self.assertFalse(fields["faculty"].required or fields["specialty"].required)
        self.assertEqual(fields["university"].label, "Муассисаи таҳсилотӣ")
        page = self.client.get(reverse("public:landing", args=[self.qn.public_id]))
        self.assertContains(page, "Хонандаи мактаб")
        self.assertContains(page, "Донишҷӯи коллеҷ / литсей")

    def test_school_pupil_can_start_without_faculty(self):
        f = {pf.key: pf.form_name for pf in self.qn.participant_fields.filter(enabled=True)}
        r = self.client.post(reverse("public:start", args=[self.qn.public_id]), {
            "consent": "on", f["education_level"]: "Хонандаи мактаб", f["university"]: "Мактаби №5, ш. Бохтар",
        })
        self.assertEqual(r.status_code, 302)
        p = Participant.objects.get()
        self.assertEqual((p.education_level, p.university, p.faculty), ("Хонандаи мактаб", "Мактаби №5, ш. Бохтар", ""))

    def test_institution_is_required_and_level_must_be_valid(self):
        f = {pf.key: pf.form_name for pf in self.qn.participant_fields.filter(enabled=True)}
        r = self.client.post(reverse("public:start", args=[self.qn.public_id]), {"consent": "on", f["education_level"]: "Invalid"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Participant.objects.exists())
