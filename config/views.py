from importlib import metadata

from django.conf import settings
from django.db import connection
from django.db.utils import DatabaseError
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

HealthSerializer = inline_serializer(
    name="Health",
    fields={
        "status": serializers.ChoiceField(choices=["ok", "degraded"]),
        "service": serializers.CharField(),
        "version": serializers.CharField(),
        "time": serializers.DateTimeField(),
        "database": serializers.DictField(child=serializers.CharField()),
        "llm": serializers.DictField(child=serializers.CharField()),
        "frameworks": serializers.DictField(child=serializers.CharField()),
        "docs": serializers.URLField(),
        "api": serializers.URLField(),
    },
)


class HealthView(APIView):
    """Server status and version. Returns 503 if the database is unreachable."""

    @extend_schema(
        summary="Server status and version",
        tags=["server"],
        responses={200: HealthSerializer, 503: HealthSerializer},
    )
    def get(self, request):
        try:
            connection.ensure_connection()
            database = "ok"
        except DatabaseError:
            database = "unavailable"

        healthy = database == "ok"
        return Response(
            {
                "status": "ok" if healthy else "degraded",
                "service": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "time": timezone.localtime().isoformat(timespec="seconds"),
                "database": {"status": database, "vendor": connection.vendor},
                "llm": {"provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL},
                "frameworks": {
                    "django": metadata.version("django"),
                    "djangorestframework": metadata.version("djangorestframework"),
                },
                "docs": request.build_absolute_uri(reverse("swagger-ui")),
                "api": request.build_absolute_uri("/api/"),
            },
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
