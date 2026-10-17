from django.urls import path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

from .views import ConceptViewSet, EntryViewSet

router = SimpleRouter()
router.register("concepts", ConceptViewSet)
router.register("entries", EntryViewSet)

urlpatterns = [
    # /api/ opens the interactive docs, which list every route and method.
    path("", RedirectView.as_view(pattern_name="swagger-ui"), name="api-root"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    *router.urls,
]
