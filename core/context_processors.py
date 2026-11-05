from django.conf import settings

from .models import SiteSettings


def site(request):
    return {
        "site_settings": SiteSettings.load(),
        "platform_name": settings.PLATFORM_NAME,
        "ai_enabled": settings.AI_PROVIDER in {"anthropic", "openai"} and bool(settings.AI_API_KEY),
    }
