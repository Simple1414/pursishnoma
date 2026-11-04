from django.conf import settings

from .models import SiteSettings


def site(request):
    return {
        "site_settings": SiteSettings.load(),
        "ai_enabled": settings.AI_PROVIDER in {"anthropic", "openai"} and bool(settings.AI_API_KEY),
    }
