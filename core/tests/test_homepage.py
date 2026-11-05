"""Homepage redesign: real statistics, study cards, working links, privacy page."""
import importlib

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.templatetags.rs_tags import doc_blocks
from surveys.models import Participant, Questionnaire, SurveyResponse
from surveys.tests.helpers import make_questionnaire, make_user


def add_response(qn: Questionnaire, completed: bool) -> None:
    p = Participant.objects.create(questionnaire=qn, consent_given=True)
    status = SurveyResponse.Status.COMPLETED if completed else SurveyResponse.Status.IN_PROGRESS
    SurveyResponse.objects.create(
        questionnaire=qn, participant=p, status=status, submitted_at=timezone.now() if completed else None
    )


@override_settings(LANGUAGE_CODE="en")
class HomepageTests(TestCase):
    def setUp(self):
        owner = make_user("owner")
        self.qn = make_questionnaire(owner, title="Open study")
        self.draft = make_questionnaire(owner, published=False, title="Hidden draft")
        add_response(self.qn, completed=True)
        add_response(self.qn, completed=True)
        add_response(self.qn, completed=False)
        add_response(self.draft, completed=True)

    def get(self):
        return self.client.get(reverse("core:home"), HTTP_ACCEPT_LANGUAGE="en")

    def test_statistics_come_from_the_database_and_skip_drafts(self):
        stats = self.get().context["public_stats"]
        self.assertEqual(stats, {"studies": 1, "participants": 3, "responses": 2, "open": 1})

    def test_study_card_counts_completed_responses_once(self):
        r = self.get()
        study = r.context["open_studies"][0]
        self.assertEqual(study.n_completed, 2)  # not multiplied by the number of questions
        self.assertEqual(study.n_questions, self.qn.questions.count())
        self.assertContains(r, "Open study")
        self.assertNotContains(r, "Hidden draft")
        self.assertContains(r, self.qn.get_public_url())

    def test_statistics_can_be_hidden(self):
        from core.models import SiteSettings

        s = SiteSettings.load()
        s.show_public_statistics = False
        s.save()
        self.assertIsNone(self.get().context["public_stats"])

    def test_public_links_point_to_real_pages(self):
        r = self.get()
        for name in ("core:guide", "core:privacy", "login"):
            self.assertContains(r, f'href="{reverse(name)}', msg_prefix=name)

    def test_no_create_research_buttons_for_visitors(self):
        r = self.get()
        self.assertNotContains(r, reverse("surveys:create"))
        self.assertNotContains(r, reverse("surveys:import"))
        # The builder itself stays protected.
        self.assertRedirects(self.client.get(reverse("surveys:create")), f"{reverse('login')}?next={reverse('surveys:create')}")

    def test_platform_name_in_title_and_header(self):
        r = self.get()
        self.assertContains(r, "· Андеша</title>", html=False)
        self.assertNotContains(r, "ResearchSurvey")

    def test_privacy_page(self):
        r = self.client.get(reverse("core:privacy"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="consent"')


class DocBlocksTests(TestCase):
    def test_instruction_text_is_split_into_typed_blocks(self):
        text = "(for students)\nDear respondent!\nThe survey is anonymous.\nHow to fill in\nTick one box.\nWrite short answers.\n"
        blocks = doc_blocks(text)
        self.assertEqual([b["kind"] for b in blocks], ["note", "greeting", "para", "heading", "list"])
        self.assertEqual(blocks[-1]["items"], ["Tick one box.", "Write short answers."])

    def test_empty(self):
        self.assertEqual(doc_blocks(""), [])
        self.assertEqual(doc_blocks(None), [])


@override_settings(LANGUAGE_CODE="en")
class StartPageHeaderTests(TestCase):
    def setUp(self):
        self.qn = make_questionnaire(make_user("owner"), title="ИСТИФОДАИ ЗЕҲНИ СУНЪӢ (версияи ислоҳшуда)")
        Questionnaire.objects.filter(pk=self.qn.pk).update(
            description="ҶУМҲУРИИ ТОҶИКИСТОН\nДОНИШГОҲИ ДАВЛАТИИ БОХТАР БА НОМИ НОСИРИ ХУСРАВ\nПУРСИШНОМАИ ИЛМӢ\nдар мавзӯи",
            instructions="Муҳтарам респондент!\nМатн.\nМаълумот дар бораи анкета (аз ҷониби муҳаққиқ пур карда мешавад)",
        )

    def test_migration_adds_ministry_emblem_and_cleans_text(self):
        mig = importlib.import_module("surveys.migrations.0008_start_page_header")
        mig.forwards(apps, None)
        mig.forwards(apps, None)  # idempotent
        self.qn.refresh_from_db()
        self.assertTrue(self.qn.show_institution_emblem)
        self.assertTrue(self.qn.description.startswith("ВАЗОРАТИ МАОРИФ ВА ИЛМИ ҶУМҲУРИИ ТОҶИКИСТОН\n"))
        self.assertEqual(self.qn.title, "ИСТИФОДАИ ЗЕҲНИ СУНЪӢ")
        self.assertEqual(self.qn.instructions, "Муҳтарам респондент!\nМатн.")

    def test_start_page_shows_emblem_only_when_enabled(self):
        url = reverse("public:landing", args=[self.qn.public_id])
        self.assertNotContains(self.client.get(url), "bsu-emblem")
        Questionnaire.objects.filter(pk=self.qn.pk).update(show_institution_emblem=True)
        r = self.client.get(url)
        self.assertContains(r, "bsu-emblem")
        self.assertContains(r, "ДОНИШГОҲИ ДАВЛАТИИ БОХТАР")
        self.assertContains(r, "rs-justify")


@override_settings(LANGUAGE_CODE="en")
class DashboardTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.mine = make_questionnaire(self.alice, title="Alice study")
        self.other = make_questionnaire(self.bob, title="Bob study")
        add_response(self.mine, completed=True)
        add_response(self.mine, completed=False)
        add_response(self.other, completed=True)
        add_response(self.other, completed=True)

    def test_figures_are_scoped_to_the_researcher(self):
        from core.dashboard import build

        dash = build(self.alice)
        self.assertEqual([k.count for k in dash.kpis[:3]], [1, 2, 1])
        self.assertEqual(dash.kpis[3].value, "50%")
        self.assertEqual([p.title for p in dash.projects], ["Alice study"])
        self.assertEqual(sum(dash.trend_chart["datasets"][1]["values"]), 1)
        self.assertTrue(all(r.questionnaire_id == self.mine.pk for r in dash.recent))

    def test_notifications_reflect_pending_reviews(self):
        from core.dashboard import build

        titles = [n.title for n in build(self.alice).notices]
        self.assertIn("Responses await review", titles)

    def test_dashboard_page_renders_for_researcher_and_is_protected(self):
        url = reverse("core:dashboard")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.client.force_login(self.alice)
        r = self.client.get(url)
        self.assertContains(r, "Alice study")
        self.assertNotContains(r, "Bob study")
        self.assertContains(r, 'id="chart-trend"')
        self.assertContains(r, 'id="notifications"')

    def test_empty_dashboard(self):
        carol = make_user("carol")
        self.client.force_login(carol)
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "No responses yet")
        self.assertNotContains(r, 'id="chart-trend"')


class InstructionsAudienceMigrationTests(TestCase):
    def test_audience_line_includes_pupils_and_teachers(self):
        qn = make_questionnaire(make_user("owner"))
        mig = importlib.import_module("surveys.migrations.0009_instructions_audience")
        Questionnaire.objects.filter(pk=qn.pk).update(instructions=f"{mig.OLD}\nМуҳтарам респондент!")
        mig.forwards(apps, None)
        qn.refresh_from_db()
        self.assertTrue(qn.instructions.startswith("(барои хонандагон, донишҷӯён ва омӯзгорони"))
