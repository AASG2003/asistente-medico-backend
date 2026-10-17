from django.contrib import admin

from .models import Concept, Entry, Question, Record


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "unit", "reviewed", "created_at"]
    list_filter = ["kind", "reviewed"]
    list_editable = ["reviewed"]
    search_fields = ["name", "slug"]
    readonly_fields = ["slug", "created_at"]


class RecordInline(admin.TabularInline):
    model = Record
    extra = 0
    fields = ["concept", "value", "time_of_day"]
    autocomplete_fields = ["concept"]


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ["text", "reason", "concept", "answer", "answered_at"]
    autocomplete_fields = ["concept"]


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ["date", "short_text", "status", "created_at"]
    list_filter = ["status", "date"]
    search_fields = ["raw_text"]
    date_hierarchy = "date"
    readonly_fields = ["llm_response", "created_at", "updated_at"]
    inlines = [RecordInline, QuestionInline]

    @admin.display(description="Text")
    def short_text(self, obj: Entry) -> str:
        return obj.raw_text[:60]


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ["date", "concept", "value", "time_of_day", "entry"]
    list_filter = ["concept__kind", "concept", "time_of_day"]
    date_hierarchy = "date"
    autocomplete_fields = ["concept", "entry"]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["text", "reason", "entry", "answered_at"]
    list_filter = ["reason"]
    autocomplete_fields = ["concept", "entry"]
