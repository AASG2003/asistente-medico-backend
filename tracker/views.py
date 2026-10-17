from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Concept, Entry
from .serializers import (
    AnswerSerializer,
    ConceptSerializer,
    DetailSerializer,
    EntrySerializer,
    FromTextSerializer,
    ProcessingErrorSerializer,
)
from .services.records import EntryProcessingError, EntryStateError, answer_questions, create_entry_from_text


@extend_schema_view(
    list=extend_schema(
        summary="List concepts",
        parameters=[
            OpenApiParameter("kind", OpenApiTypes.STR, enum=Concept.Kind.values, description="Filter by kind."),
            OpenApiParameter("reviewed", OpenApiTypes.BOOL, description="Filter by review status."),
        ],
    ),
    retrieve=extend_schema(summary="Get a concept"),
    create=extend_schema(summary="Create a concept"),
    update=extend_schema(summary="Replace a concept"),
    partial_update=extend_schema(summary="Update a concept (e.g. mark reviewed, add aliases)"),
)
class ConceptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Catalog of concepts. Gemma adds new ones automatically (reviewed=false);
    here they can be listed, created, renamed or given aliases.
    No delete: concepts with records are protected.
    """

    queryset = Concept.objects.all()
    serializer_class = ConceptSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if kind := self.request.query_params.get("kind"):
            queryset = queryset.filter(kind=kind)
        if (reviewed := self.request.query_params.get("reviewed")) is not None:
            queryset = queryset.filter(reviewed=reviewed.lower() in {"1", "true", "yes"})
        return queryset


@extend_schema_view(
    list=extend_schema(
        summary="List entries",
        parameters=[
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Entries on or after this date."),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Entries on or before this date."),
            OpenApiParameter("status", OpenApiTypes.STR, enum=Entry.Status.values, description="Filter by status."),
        ],
    ),
    retrieve=extend_schema(summary="Get an entry"),
    destroy=extend_schema(summary="Delete an entry and its records"),
)
class EntryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Entries with their extracted records and follow-up questions.
    Entries are created only from text (POST /entries/from-text/).
    """

    queryset = Entry.objects.prefetch_related("records__concept", "questions__concept")
    serializer_class = EntrySerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if date_from := params.get("date_from"):
            queryset = queryset.filter(date__gte=_parse_date("date_from", date_from))
        if date_to := params.get("date_to"):
            queryset = queryset.filter(date__lte=_parse_date("date_to", date_to))
        if entry_status := params.get("status"):
            queryset = queryset.filter(status=entry_status)
        return queryset

    def _entry_response(self, entry: Entry, response_status: int) -> Response:
        entry = self.get_queryset().get(pk=entry.pk)  # reload with records and questions
        data = EntrySerializer(entry, context=self.get_serializer_context()).data
        return Response(data, status=response_status)

    @extend_schema(
        summary="Create an entry from free text",
        description=(
            "Gemma extracts records from the text. If a quantity or intensity is missing, "
            "or the text is vague, the entry is saved with status `pending_questions` and "
            "the questions to answer are included in the response."
        ),
        request=FromTextSerializer,
        responses={
            201: EntrySerializer,
            400: OpenApiTypes.OBJECT,
            502: ProcessingErrorSerializer,
        },
        examples=[
            OpenApiExample(
                "Note with missing values",
                value={"text": "Dormí 5 horas, tomé café y me duele la cabeza desde la tarde"},
                request_only=True,
            ),
            OpenApiExample(
                "Vague note",
                value={"text": "Me sentí raro en la noche", "date": "2026-10-16"},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="from-text", serializer_class=FromTextSerializer)
    def from_text(self, request):
        """Text -> Gemma -> records (+ questions if something is missing)."""
        serializer = FromTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            entry = create_entry_from_text(serializer.validated_data["text"], serializer.validated_data.get("date"))
        except EntryProcessingError as error:
            return Response(
                {"detail": str(error), "entry": EntrySerializer(error.entry).data},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return self._entry_response(entry, status.HTTP_201_CREATED)

    @extend_schema(
        summary="Answer follow-up questions",
        description=(
            "Saves the answers and runs a second (and last) extraction. Blank answers are "
            "skipped. Afterwards the entry is `complete`."
        ),
        request=AnswerSerializer,
        responses={
            200: EntrySerializer,
            400: OpenApiTypes.OBJECT,
            404: DetailSerializer,
            409: DetailSerializer,
            502: DetailSerializer,
        },
        examples=[
            OpenApiExample(
                "Answers",
                value={"answers": [{"question": 1, "answer": "3 tazas"}, {"question": 2, "answer": "6"}]},
                request_only=True,
            ),
        ],
    )
    @action(detail=True, methods=["post"], serializer_class=AnswerSerializer)
    def answer(self, request, pk=None):
        """Answers follow-up questions and completes the entry."""
        entry = self.get_object()
        serializer = AnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        answers = {item["question"]: item["answer"] for item in serializer.validated_data["answers"]}
        try:
            entry = answer_questions(entry, answers)
        except EntryStateError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        except ValueError as error:
            raise ValidationError({"answers": [str(error)]}) from error
        except EntryProcessingError as error:
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)
        return self._entry_response(entry, status.HTTP_200_OK)


def _parse_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError({name: ["Use the format YYYY-MM-DD."]}) from error
