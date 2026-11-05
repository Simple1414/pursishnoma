from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog, User
from surveys.tests.helpers import PASSWORD, make_questionnaire, make_user


class PublicPagesTests(TestCase):
    def test_homepage(self):
        make_questionnaire(make_user("alice"), title="Open study")
        r = self.client.get(reverse("core:home"))
        self.assertContains(r, "Андеша")
        self.assertContains(r, "Open study")
        self.assertIn("Content-Security-Policy", r.headers)
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")

    def test_healthz(self):
        self.assertEqual(self.client.get(reverse("core:healthz")).content, b"ok")

    def test_language_switch(self):
        r = self.client.post(reverse("set_language"), {"language": "ru", "next": "/"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get("/"), 'lang="ru"')

    def test_404_page(self):
        self.assertEqual(self.client.get("/does-not-exist/").status_code, 404)


class AuthTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")

    def test_login_and_dashboard(self):
        r = self.client.post(reverse("login"), {"username": "alice", "password": PASSWORD})
        self.assertRedirects(r, reverse("core:dashboard"))
        self.assertContains(self.client.get(reverse("core:dashboard")), "rs-stat")
        self.assertTrue(AuditLog.objects.filter(action="login.success").exists())

    def test_failed_login_is_logged(self):
        self.client.post(reverse("login"), {"username": "alice", "password": "wrong"})
        self.assertTrue(AuditLog.objects.filter(action="login.failed").exists())

    @override_settings(RATE_LIMIT_LOGIN="3/900")
    def test_login_rate_limit(self):
        for _ in range(3):
            self.client.post(reverse("login"), {"username": "alice", "password": "wrong"})
        r = self.client.post(reverse("login"), {"username": "alice", "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="login.rate_limited").exists())

    def test_logout_requires_post(self):
        self.client.login(username="alice", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 302)

    def test_passwords_are_hashed(self):
        self.assertNotEqual(User.objects.get(username="alice").password, PASSWORD)


class AdminAreaTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", role=User.Role.ADMIN)
        self.researcher = make_user("res")

    def test_researcher_cannot_open_admin_pages(self):
        self.client.login(username="res", password=PASSWORD)
        for name in ("core:users", "core:user_create", "core:settings", "core:security"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("core:user_edit", args=[self.admin.pk])).status_code, 403)

    def test_admin_creates_user_and_changes_role(self):
        self.client.login(username="admin", password=PASSWORD)
        r = self.client.post(reverse("core:user_create"), {
            "username": "newbie", "first_name": "N", "last_name": "B", "email": "n@example.org",
            "role": "researcher", "affiliation": "BSU", "preferred_language": "tg", "password": "An0ther-Str0ng-Pass",
        })
        self.assertRedirects(r, reverse("core:users"))
        user = User.objects.get(username="newbie")
        self.assertTrue(user.check_password("An0ther-Str0ng-Pass"))
        self.client.post(reverse("core:user_edit", args=[user.pk]), {"first_name": "N", "last_name": "B", "email": "n@example.org", "role": "admin", "affiliation": "", "preferred_language": "tg", "is_active": "on"})
        user.refresh_from_db()
        self.assertEqual(user.role, "admin")
        self.assertTrue(AuditLog.objects.filter(action="user.update").exists())

    def test_weak_password_rejected(self):
        self.client.login(username="admin", password=PASSWORD)
        self.client.post(reverse("core:user_create"), {"username": "weak", "role": "researcher", "preferred_language": "tg", "password": "123"})
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_admin_cannot_demote_self(self):
        self.client.login(username="admin", password=PASSWORD)
        self.client.post(reverse("core:user_edit", args=[self.admin.pk]), {"role": "researcher", "preferred_language": "tg", "is_active": "on"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, "admin")

    def test_security_and_settings_pages(self):
        self.client.login(username="admin", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("core:security")).status_code, 200)
        r = self.client.post(reverse("core:settings"), {"organization_name": "BSU", "tagline": "T", "contact_email": "info@example.org", "show_public_statistics": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get("/"), "info@example.org")


class EnsureAdminCommandTests(TestCase):
    def test_creates_admin_once(self):
        with self.settings():
            import os

            os.environ.update({"DJANGO_ADMIN_USERNAME": "boss", "DJANGO_ADMIN_PASSWORD": "Very-Str0ng-Admin-Pass"})
            try:
                call_command("ensure_admin")
                call_command("ensure_admin")
            finally:
                os.environ.pop("DJANGO_ADMIN_USERNAME")
                os.environ.pop("DJANGO_ADMIN_PASSWORD")
        user = User.objects.get(username="boss")
        self.assertTrue(user.is_platform_admin)


class VideoGuideTests(TestCase):
    def test_guide_page_is_public_and_linked(self):
        r = self.client.get(reverse("core:guide"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "video/rs-dastur.mp4")
        self.assertContains(r, "playsinline")
        self.assertContains(self.client.get("/"), reverse("core:guide"))
        qn = make_questionnaire(make_user("alice"))
        self.assertContains(self.client.get(reverse("public:landing", args=[qn.public_id])), reverse("core:guide"))
