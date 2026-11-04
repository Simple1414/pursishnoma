from django.urls import path

from . import views

app_name = "surveys"

urlpatterns = [
    path("", views.questionnaire_list, name="list"),
    path("new/", views.questionnaire_create, name="create"),
    path("import/", views.questionnaire_import, name="import"),
    path("responses/", views.response_list, name="responses"),
    path("responses/compare/", views.response_compare, name="compare"),
    path("responses/<int:pk>/", views.response_detail, name="response_detail"),
    path("participants/", views.participant_list, name="participants"),
    path("<int:pk>/", views.questionnaire_detail, name="detail"),
    path("<int:pk>/edit/", views.questionnaire_edit, name="edit"),
    path("<int:pk>/duplicate/", views.questionnaire_duplicate, name="duplicate"),
    path("<int:pk>/delete/", views.questionnaire_delete, name="delete"),
    path("<int:pk>/status/<str:action>/", views.questionnaire_status, name="status"),
    path("<int:pk>/qr.png", views.questionnaire_qr, name="qr"),
    path("<int:pk>/preview/", views.questionnaire_preview, name="preview"),
    path("<int:pk>/source.docx", views.questionnaire_source, name="source"),
    path("<int:pk>/participant-fields/", views.participant_fields, name="participant_fields"),
    path("<int:pk>/sections/add/", views.section_add, name="section_add"),
    path("<int:pk>/sections/<int:section_id>/", views.section_edit, name="section_edit"),
    path("<int:pk>/sections/<int:section_id>/delete/", views.section_delete, name="section_delete"),
    path("<int:pk>/sections/<int:section_id>/move/<str:direction>/", views.section_move, name="section_move"),
    path("<int:pk>/questions/new/", views.question_edit, name="question_add"),
    path("<int:pk>/questions/<int:question_id>/", views.question_edit, name="question_edit"),
    path("<int:pk>/questions/<int:question_id>/delete/", views.question_delete, name="question_delete"),
    path("<int:pk>/questions/<int:question_id>/move/<str:direction>/", views.question_move, name="question_move"),
]
