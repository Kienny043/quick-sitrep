from django.contrib import admin
from django.urls import include, path

from apps.quickentry.views import main_page, signatory_settings, batch_history

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/", include("apps.quickentry.urls")),
    path("settings/", signatory_settings, name="quickentry-settings"),
    path("history/", batch_history, name="quickentry-history"),
    path("", main_page, name="quickentry-main"),
]
