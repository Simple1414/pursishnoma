from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import RateLimitedLoginView

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password/change/",
        auth_views.PasswordChangeView.as_view(success_url="/app/"),
        name="password_change",
    ),
    path("django-admin/", admin.site.urls),
    path("api/v1/", include("surveys.api_urls")),
    path("s/", include("surveys.public_urls")),
    path("app/questionnaires/", include("surveys.urls")),
    path("app/evaluation/", include("evaluation.urls")),
    path("app/analytics/", include("analytics.urls")),
    path("app/reports/", include("reports.urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.error_403"
handler404 = "core.views.error_404"
handler500 = "core.views.error_500"

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
