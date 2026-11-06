from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


class SecurityHeadersMiddleware:
    """Adds a conservative Content-Security-Policy and Permissions-Policy.

    All scripts, styles and fonts are served from our own origin (vendored),
    so no third-party hosts are needed.
    """

    CSP = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-eval'; "  # Alpine.js evaluates directive expressions
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


class TajikFirstLocaleMiddleware(LocaleMiddleware):
    """Always start in the site language (Tajik); switch only on the visitor's own choice.

    Django's ``LocaleMiddleware`` follows the browser's ``Accept-Language``
    header, so a phone set to Russian would open the site in Russian. Here the
    language comes only from the cookie written by the language switcher
    (``set_language``); without it the default ``LANGUAGE_CODE`` is used.
    """

    def process_request(self, request):
        chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        try:
            language = translation.get_supported_language_variant(chosen) if chosen else settings.LANGUAGE_CODE
        except LookupError:
            language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
