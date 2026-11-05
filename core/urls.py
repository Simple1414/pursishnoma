from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("guide/", views.guide, name="guide"),
    path("privacy/", views.privacy, name="privacy"),
    path("app/", views.dashboard, name="dashboard"),
    path("app/users/", views.user_list, name="users"),
    path("app/users/new/", views.user_create, name="user_create"),
    path("app/users/<int:pk>/", views.user_edit, name="user_edit"),
    path("app/settings/", views.site_settings, name="settings"),
    path("app/security/", views.security, name="security"),
]
