from django.urls import path

from . import public_views as views

app_name = "public"

urlpatterns = [
    path("<uuid:public_id>/", views.landing, name="landing"),
    path("<uuid:public_id>/start/", views.start, name="start"),
    path("<uuid:public_id>/fill/", views.fill, name="fill"),
    path("<uuid:public_id>/save/", views.autosave, name="autosave"),
    path("<uuid:public_id>/submit/", views.submit, name="submit"),
    path("<uuid:public_id>/done/", views.done, name="done"),
]
