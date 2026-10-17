"""
Saves what the LLM extracted. This is the only place that writes entries,
records and questions, so views stay thin and the rules live in one spot.

The LLM call happens outside database transactions (it can take seconds);
only the writes are wrapped in `transaction.atomic()`.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import Concept, Entry, Question, Record
from ..text import make_slug, normalize_name
from .context import load_context
from .extraction import AnsweredQuestion, Extraction, ExtractionError, extract
from .llm import LLMClient, LLMError, get_llm_client


class EntryProcessingError(Exception):
    """The LLM step failed. The entry (and the user's text) is still saved."""

    def __init__(self, message: str, entry: Entry | None = None, details: dict | None = None):
        super().__init__(message)
        self.entry = entry
        self.details = details or {"error": message}


class EntryStateError(Exception):
    """The entry is not in a state that allows this operation."""


# Concepts --------------------------------------------------------------------------


def find_concept(name: str) -> Concept | None:
    """Finds a concept by canonical name or alias, without creating it."""
    normalized = normalize_name(name)
    concept = Concept.objects.filter(slug=make_slug(normalized)).first()
    if concept:
        return concept
    # The catalog is small, so aliases are matched in Python. This avoids JSON
    # queries, which behave differently on SQLite and PostgreSQL.
    for candidate in Concept.objects.all():
        if normalized in candidate.aliases:
            return candidate
    return None


def resolve_concept(name: str, kind: str, unit: str = "") -> Concept:
    """Returns the matching concept, creating an unreviewed one if it is new."""
    concept = find_concept(name)
    if concept is None:
        concept, _ = Concept.objects.get_or_create(
            slug=make_slug(name),
            defaults={"name": name, "kind": kind, "unit": unit, "reviewed": False},
        )
    elif unit and not concept.unit and concept.kind == Concept.Kind.HABIT:
        concept.unit = unit
        concept.save(update_fields=["unit"])
    return concept


# Saving ------------------------------------------------------------------------------


def _llm_log(extraction: Extraction) -> dict:
    return {
        "raw_response": extraction.raw_response,
        "result": extraction.result.model_dump(),
        "attempts": extraction.attempts,
        "durations": [round(seconds, 2) for seconds in extraction.durations],
    }


def _save_records(entry: Entry, extraction: Extraction) -> dict[str, Concept]:
    """Creates or updates one record per concept. Returns concepts by normalized name."""
    concepts: dict[str, Concept] = {}
    for item in extraction.result.records:
        concept = resolve_concept(item.concept, item.kind, item.unit)
        concepts[normalize_name(item.concept)] = concept
        Record.objects.update_or_create(
            entry=entry,
            concept=concept,
            defaults={
                "date": entry.date,
                "value": None if item.value is None else Decimal(str(item.value)),
                "time_of_day": item.time_of_day or "",
            },
        )
    return concepts


def save_extraction(entry: Entry, extraction: Extraction) -> Entry:
    """Stores records and follow-up questions from the first extraction."""
    with transaction.atomic():
        concepts = _save_records(entry, extraction)
        for item in extraction.result.questions:
            concept = None
            if item.concept:
                # Questions never create concepts; they only point to known ones.
                concept = concepts.get(normalize_name(item.concept)) or find_concept(item.concept)
            Question.objects.create(entry=entry, concept=concept, text=item.text, reason=item.reason)

        entry.status = (
            Entry.Status.PENDING_QUESTIONS if extraction.result.questions else Entry.Status.COMPLETE
        )
        entry.llm_response = _llm_log(extraction)
        entry.save(update_fields=["status", "llm_response", "updated_at"])
    return entry


def _run_extraction(entry: Entry, client: LLMClient | None, answers: list[AnsweredQuestion] | None = None):
    try:
        # get_llm_client() is inside the try: a bad configuration is an LLM error too.
        client = client or get_llm_client()
        return extract(entry.raw_text, entry.date, load_context(entry.date), client, answers)
    except ExtractionError as error:
        details = {"error": str(error), "raw_responses": error.raw_responses}
        raise EntryProcessingError(str(error), entry, details) from error
    except LLMError as error:
        raise EntryProcessingError(str(error), entry) from error


# Use cases -------------------------------------------------------------------------


def create_entry_from_text(text: str, entry_date: date | None = None, client: LLMClient | None = None) -> Entry:
    """
    Saves the text first (so it is never lost), then asks the LLM to structure it.
    Raises EntryProcessingError if the LLM fails; the entry stays with status "error".
    """
    entry = Entry.objects.create(
        date=entry_date or timezone.localdate(),
        raw_text=text.strip(),
        status=Entry.Status.ERROR,  # until the extraction succeeds
    )
    try:
        extraction = _run_extraction(entry, client)
    except EntryProcessingError as error:
        entry.llm_response = error.details
        entry.save(update_fields=["llm_response", "updated_at"])
        raise
    return save_extraction(entry, extraction)


def answer_questions(entry: Entry, answers: dict[int, str], client: LLMClient | None = None) -> Entry:
    """
    Stores the answers and runs a second (and last) extraction with them.
    Blank answers are skipped. If the LLM fails, answers are kept and the entry
    stays pending, so the call can be retried.
    """
    if entry.status != Entry.Status.PENDING_QUESTIONS:
        raise EntryStateError(f"Entry {entry.pk} has no pending questions (status: {entry.status}).")

    questions = {question.pk: question for question in entry.questions.all()}
    unknown = set(answers) - set(questions)
    if unknown:
        raise ValueError(f"Questions {sorted(unknown)} do not belong to entry {entry.pk}.")

    now = timezone.now()
    with transaction.atomic():
        for question_id, answer in answers.items():
            if not answer.strip():
                continue
            question = questions[question_id]
            question.answer = answer.strip()
            question.answered_at = now
            question.save(update_fields=["answer", "answered_at"])

    answered = [
        AnsweredQuestion(
            concept=question.concept.name if question.concept else None,
            text=question.text,
            answer=question.answer,
        )
        for question in questions.values()
        if question.is_answered
    ]

    try:
        extraction = _run_extraction(entry, client, answered)
    except EntryProcessingError as error:
        # Keep the first extraction log; the entry stays pending for a retry.
        entry.llm_response = {**(entry.llm_response or {}), "answers_error": error.details}
        entry.save(update_fields=["llm_response", "updated_at"])
        raise

    with transaction.atomic():
        # Merge: update or add records, never delete what the first round saved.
        _save_records(entry, extraction)
        # One round only: questions from the second extraction are ignored.
        entry.status = Entry.Status.COMPLETE
        entry.llm_response = {"first": entry.llm_response, "answers": _llm_log(extraction)}
        entry.save(update_fields=["status", "llm_response", "updated_at"])
    return entry
