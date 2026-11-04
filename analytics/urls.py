from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.overview, name="overview_pick"),
    path("<int:pk>/", views.overview, name="overview"),
    path("<int:pk>/crosstab/", views.crosstab, name="crosstab"),
    path("<int:pk>/compare/", views.compare, name="compare"),
    path("intelligence/", views.intelligence_center, name="intelligence_pick"),
    path("intelligence/<int:pk>/", views.intelligence_center, name="intelligence"),
    path("intelligence/<int:pk>/generate/", views.generate_findings, name="generate_findings"),
    path("intelligence/<int:pk>/findings/add/", views.finding_add, name="finding_add"),
    path("intelligence/<int:pk>/findings/<int:finding_id>/", views.finding_review, name="finding_review"),
    path("assistant/", views.assistant, name="assistant_pick"),
    path("assistant/<int:pk>/", views.assistant, name="assistant"),
    path("assistant/<int:pk>/<int:analysis_id>/", views.assistant_review, name="assistant_review"),
]
