from django.urls import path

from . import views

app_name = "evaluation"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("responses/<int:pk>/", views.evaluate, name="evaluate"),
    path("responses/<int:pk>/reviewed/", views.mark_reviewed, name="mark_reviewed"),
    path("questionnaires/<int:pk>/criteria/", views.criteria, name="criteria"),
]
