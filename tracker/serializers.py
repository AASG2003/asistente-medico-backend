from django.utils import timezone
from rest_framework import serializers

from .models import Concept, Entry, Question, Record


class ConceptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Concept
        fields = ["id", "name", "slug", "kind", "unit", "aliases", "reviewed", "created_at"]
        read_only_fields = ["slug", "created_at"]

    def validate_aliases(self, value):
        if not all(isinstance(alias, str) for alias in value):
            raise serializers.ValidationError("Aliases must be a list of strings.")
        return value


class RecordSerializer(serializers.ModelSerializer):
    concept_name = serializers.CharField(source="concept.name", read_only=True)
    kind = serializers.CharField(source="concept.kind", read_only=True)
    unit = serializers.CharField(source="concept.unit", read_only=True)

    class Meta:
        model = Record
        fields = ["id", "concept", "concept_name", "kind", "unit", "date", "value", "time_of_day"]
        read_only_fields = fields


class QuestionSerializer(serializers.ModelSerializer):
    concept_name = serializers.CharField(source="concept.name", read_only=True, default=None)

    class Meta:
        model = Question
        fields = ["id", "text", "reason", "concept", "concept_name", "answer", "answered_at"]
        read_only_fields = fields


class EntrySerializer(serializers.ModelSerializer):
    records = RecordSerializer(many=True, read_only=True)
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Entry
        fields = ["id", "date", "raw_text", "status", "records", "questions", "created_at", "updated_at"]
        read_only_fields = fields


class DetailSerializer(serializers.Serializer):
    """Error body: {"detail": "..."}."""

    detail = serializers.CharField()


class ProcessingErrorSerializer(DetailSerializer):
    """The LLM failed; the entry was still saved with status "error"."""

    entry = EntrySerializer()


# Input serializers for the custom actions ------------------------------------------


class FromTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000, trim_whitespace=True)
    date = serializers.DateField(required=False)

    def validate_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The date cannot be in the future.")
        return value


class AnswerItemSerializer(serializers.Serializer):
    question = serializers.IntegerField()
    answer = serializers.CharField(max_length=500, allow_blank=True, trim_whitespace=True)


class AnswerSerializer(serializers.Serializer):
    answers = AnswerItemSerializer(many=True, allow_empty=False)

    def validate_answers(self, value):
        ids = [item["question"] for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each question can be answered only once per request.")
        return value
