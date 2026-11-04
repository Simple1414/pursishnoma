"""Create the first administrator from environment variables (idempotent).

DJANGO_ADMIN_USERNAME, DJANGO_ADMIN_PASSWORD and optionally DJANGO_ADMIN_EMAIL.
Used on platforms without shell access; the password is never printed.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the initial administrator account if it does not exist."

    def handle(self, *args, **options):
        username = os.getenv("DJANGO_ADMIN_USERNAME", "").strip()
        password = os.getenv("DJANGO_ADMIN_PASSWORD", "")
        email = os.getenv("DJANGO_ADMIN_EMAIL", "")
        if not username or not password:
            self.stdout.write("DJANGO_ADMIN_USERNAME / DJANGO_ADMIN_PASSWORD not set — skipping.")
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Administrator '{username}' already exists.")
            return
        try:
            validate_password(password)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"DJANGO_ADMIN_PASSWORD is too weak: {exc}") from exc
        User.objects.create_superuser(username=username, email=email, password=password, role=User.Role.ADMIN)
        self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' created."))
