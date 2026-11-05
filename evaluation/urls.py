from django.urls import path

from . import views

app_name = "evaluation"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("auto/", views.run_auto, name="run_auto"),
    path("responses/<int:pk>/", views.evaluate, name="evaluate"),
    path("responses/<int:pk>/reviewed/", views.mark_reviewed, name="mark_reviewed"),
    path("questionnaires/<int:pk>/criteria/", views.criteria, name="criteria"),
    path("questionnaires/<int:pk>/criteria/copy/", views.copy_criteria, name="copy_criteria"),
]
