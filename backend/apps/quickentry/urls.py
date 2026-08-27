from django.urls import path

from . import views

urlpatterns = [
    path("ai-status/", views.ai_status, name="quickentry-ai-status"),
    path("current-batch/", views.current_batch, name="quickentry-current-batch"),
    path("entries/extract/", views.extract_entry, name="quickentry-entries-extract"),
    path("entries/<int:pk>/", views.entry_detail, name="quickentry-entry-detail"),
    path("entries/save/", views.save_entry, name="quickentry-entries-save"),
    path(
        "batches/<int:pk>/generate-synopsis/",
        views.generate_synopsis_view,
        name="quickentry-batch-generate-synopsis",
    ),
    path(
        "batches/<int:pk>/generate-weather/",
        views.generate_weather_view,
        name="quickentry-batch-generate-weather",
    ),
    path("batches/<int:pk>/amend/", views.amend_batch, name="quickentry-batch-amend"),
    path(
        "batches/<int:pk>/amendments/",
        views.batch_amendments,
        name="quickentry-batch-amendments",
    ),
    path("batches/<int:pk>/finalize/", views.finalize_batch, name="quickentry-batch-finalize"),
    path("batches/<int:pk>/download/", views.download_batch, name="quickentry-batch-download"),
]
