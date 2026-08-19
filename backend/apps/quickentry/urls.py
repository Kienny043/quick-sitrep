from django.urls import path

from . import views

urlpatterns = [
    path("ai-status/", views.ai_status, name="quickentry-ai-status"),
    path("current-batch/", views.current_batch, name="quickentry-current-batch"),
    path("entries/extract/", views.extract_entry, name="quickentry-entries-extract"),
    path("entries/<int:pk>/", views.entry_detail, name="quickentry-entry-detail"),
    path("entries/save/", views.save_entry, name="quickentry-entries-save"),
    path("batches/<int:pk>/finalize/", views.finalize_batch, name="quickentry-batch-finalize"),
    path("batches/<int:pk>/download/", views.download_batch, name="quickentry-batch-download"),
]
