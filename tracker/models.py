from django.db import models
from django.utils import timezone

from .text import make_slug, normalize_name


class Concept(models.Model):
    """Something that can be tracked over time: a habit, a symptom or a state."""

    class Kind(models.TextChoices):
        HABIT = "habit", "Habit"
        SYMPTOM = "symptom", "Symptom"
        STATE = "state", "State"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Canonical unit for quantities (hours, cups, minutes). Symptoms and states
    # use an intensity scale from 0 to 10, so they leave this empty.
    unit = models.CharField(max_length=30, blank=True)
    # Normalized synonyms that resolve to this concept, e.g. ["cefalea", "migrana"].
    aliases = models.JSONField(default=list, blank=True)
    # False when the LLM created it; a person can confirm or merge it later.
    reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = normalize_name(self.name)
        if not self.slug:
            self.slug = make_slug(self.name)
        self.aliases = sorted({normalize_name(alias) for alias in self.aliases if alias})
        super().save(*args, **kwargs)


class Entry(models.Model):
    """A free-text note written by the user, plus what the LLM extracted from it."""

    class Status(models.TextChoices):
        COMPLETE = "complete", "Complete"
        PENDING_QUESTIONS = "pending_questions", "Pending questions"
        ERROR = "error", "Error"

    date = models.DateField(default=timezone.localdate)
    raw_text = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETE)
    # Raw LLM output, kept for debugging prompts.
    llm_response = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name_plural = "entries"

    def __str__(self) -> str:
        return f"{self.date}: {self.raw_text[:50]}"


class Record(models.Model):
    """One structured fact extracted from an entry: concept + optional value."""

    class TimeOfDay(models.TextChoices):
        MORNING = "morning", "Morning"
        AFTERNOON = "afternoon", "Afternoon"
        NIGHT = "night", "Night"

    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="records")
    concept = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="records")
    # Copied from the entry so time-based queries do not need a join.
    date = models.DateField()
    # Quantity in the concept unit, or intensity 0-10. Empty when still unknown.
    value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    time_of_day = models.CharField(max_length=10, choices=TimeOfDay.choices, blank=True)

    class Meta:
        ordering = ["-date", "concept__name"]
        constraints = [
            models.UniqueConstraint(fields=["entry", "concept"], name="unique_concept_per_entry"),
        ]
        indexes = [models.Index(fields=["date", "concept"])]

    def __str__(self) -> str:
        value = "" if self.value is None else f" = {self.value}"
        return f"{self.date} {self.concept}{value}"

    def save(self, *args, **kwargs):
        if self.date is None:
            self.date = self.entry.date
        super().save(*args, **kwargs)


class Question(models.Model):
    """A follow-up question the LLM asks when the entry is vague or incomplete."""

    class Reason(models.TextChoices):
        MISSING_QUANTITY = "missing_quantity", "Missing quantity"
        MISSING_INTENSITY = "missing_intensity", "Missing intensity"
        AMBIGUOUS = "ambiguous", "Ambiguous"
        HABITUAL = "habitual", "Habitual concept not mentioned"

    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="questions")
    concept = models.ForeignKey(
        Concept, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )
    text = models.CharField(max_length=255)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.text

    @property
    def is_answered(self) -> bool:
        return self.answered_at is not None
