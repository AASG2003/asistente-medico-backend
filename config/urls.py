from django.contrib import admin
from django.urls import include, path

from .views import HealthView

urlpatterns = [
    path("", HealthView.as_view(), name="health"),
    path("admin/", admin.site.urls),
    path("api/", include("tracker.urls")),
]
