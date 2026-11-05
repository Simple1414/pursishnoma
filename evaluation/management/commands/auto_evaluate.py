"""Automatically evaluate completed responses that have no evaluation yet (idempotent)."""
from django.core.management.base import BaseCommand

from evaluation.services import auto_evaluate_pending


class Command(BaseCommand):
    help = "Score completed, not yet evaluated responses with the criteria's automatic rules."

    def handle(self, *args, **options):
        n = auto_evaluate_pending()
        self.stdout.write(self.style.SUCCESS(f"automatic evaluation: {n} response(s) scored."))
