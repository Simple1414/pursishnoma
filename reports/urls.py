from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_list, name="list"),
    path("new/", views.report_create, name="create"),
    path("<int:pk>/", views.report_edit, name="edit"),
    path("<int:pk>/preview/", views.report_preview, name="preview"),
    path("<int:pk>/export/<str:fmt>/", views.report_export, name="export"),
    path("<int:pk>/delete/", views.report_delete, name="delete"),
    path("exports/", views.export_center, name="exports"),
    path("exports/questionnaire/<int:pk>/<str:kind>/", views.export_dataset, name="export_dataset"),
    path("exports/response/<int:pk>/<str:fmt>/", views.export_response, name="export_response"),
    path("exports/selected/", views.export_selected, name="export_selected"),
]
