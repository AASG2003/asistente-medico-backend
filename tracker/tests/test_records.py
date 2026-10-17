import json
from datetime import date
from decimal import Decimal

from django.test import TestCase

from tracker.models import Concept, Entry, Question, Record
from tracker.services.llm import FakeClient, LLMError
from tracker.services.records import (
    EntryProcessingError,
    EntryStateError,
    answer_questions,
    create_entry_from_text,
    find_concept,
    resolve_concept,
)

ENTRY_DATE = date(2026, 10, 16)


class FailingClient:
    def generate(self, system_prompt, user_prompt):
        raise LLMError("Gemini API error 503: overloaded")


class ResolveConceptTests(TestCase):
    def test_accents_and_case_resolve_to_the_same_concept(self):
        first = resolve_concept("Sueño", "habit", "hours")
        second = resolve_concept("sueno", "habit", "hours")
        self.assertEqual(first, second)
        self.assertEqual(first.name, "sueno")
        self.assertFalse(first.reviewed)
        self.assertEqual(Concept.objects.count(), 1)

    def test_alias_resolves_to_the_canonical_concept(self):
        headache = Concept.objects.create(name="dolor de cabeza", kind="symptom", aliases=["Cefalea"])
        self.assertEqual(resolve_concept("cefalea", "symptom"), headache)
        self.assertEqual(Concept.objects.count(), 1)

    def test_fills_missing_unit_for_habits(self):
        Concept.objects.create(name="cafe", kind="habit")
        self.assertEqual(resolve_concept("cafe", "habit", "cups").unit, "cups")

    def test_find_concept_never_creates(self):
        self.assertIsNone(find_concept("agua"))
        self.assertEqual(Concept.objects.count(), 0)


class CreateEntryTests(TestCase):
    def test_saves_records_and_questions(self):
        entry = create_entry_from_text(
            "Dormí 5 horas, tomé café y me duele la cabeza desde la tarde", ENTRY_DATE, FakeClient()
        )
        self.assertEqual(entry.status, Entry.Status.PENDING_QUESTIONS)
        records = {r.concept.name: r for r in entry.records.all()}
        self.assertEqual(records["sueno"].value, Decimal("5"))
        self.assertIsNone(records["cafe"].value)
        self.assertEqual(records["dolor de cabeza"].time_of_day, "afternoon")
        self.assertEqual(records["sueno"].date, ENTRY_DATE)
        # Questions point to the concepts they are about.
        self.assertEqual(
            {q.concept.name for q in entry.questions.all()}, {"cafe", "dolor de cabeza"}
        )
        self.assertEqual(entry.llm_response["attempts"], 1)

    def test_complete_when_nothing_is_missing(self):
        entry = create_entry_from_text("Dormí 7 horas y tomé 2 cafés", ENTRY_DATE, FakeClient())
        self.assertEqual(entry.status, Entry.Status.COMPLETE)
        self.assertEqual(entry.questions.count(), 0)

    def test_reuses_concepts_across_entries(self):
        create_entry_from_text("Dormí 7 horas", ENTRY_DATE, FakeClient())
        create_entry_from_text("Dormí 6 horas", date(2026, 10, 17), FakeClient())
        self.assertEqual(Concept.objects.filter(slug="sueno").count(), 1)
        self.assertEqual(Record.objects.filter(concept__slug="sueno").count(), 2)

    def test_ambiguous_text_creates_no_concepts(self):
        entry = create_entry_from_text("Me sentí raro en la noche", ENTRY_DATE, FakeClient())
        self.assertEqual(Concept.objects.count(), 0)
        question = entry.questions.get()
        self.assertEqual(question.reason, Question.Reason.AMBIGUOUS)
        self.assertIsNone(question.concept)

    def test_llm_failure_keeps_the_text(self):
        with self.assertRaises(EntryProcessingError) as caught:
            create_entry_from_text("Dormí 5 horas", ENTRY_DATE, FailingClient())
        entry = Entry.objects.get()
        self.assertEqual(caught.exception.entry, entry)
        self.assertEqual(entry.status, Entry.Status.ERROR)
        self.assertEqual(entry.raw_text, "Dormí 5 horas")
        self.assertIn("503", entry.llm_response["error"])

    def test_invalid_llm_output_keeps_raw_responses(self):
        with self.assertRaises(EntryProcessingError):
            create_entry_from_text("Dormí 5 horas", ENTRY_DATE, FakeClient(responses=["nope", "nope"]))
        self.assertEqual(Entry.objects.get().llm_response["raw_responses"], ["nope", "nope"])


class AnswerQuestionsTests(TestCase):
    def setUp(self):
        self.entry = create_entry_from_text(
            "Dormí 5 horas, tomé café y me duele la cabeza", ENTRY_DATE, FakeClient()
        )
        self.questions = {q.concept.name: q for q in self.entry.questions.all()}

    def test_answers_fill_values_and_complete_the_entry(self):
        entry = answer_questions(
            self.entry,
            {self.questions["cafe"].pk: "3", self.questions["dolor de cabeza"].pk: "7"},
            FakeClient(),
        )
        self.assertEqual(entry.status, Entry.Status.COMPLETE)
        values = {r.concept.name: r.value for r in entry.records.all()}
        self.assertEqual(values, {"sueno": Decimal("5"), "cafe": Decimal("3"), "dolor de cabeza": Decimal("7")})
        self.assertTrue(all(q.is_answered for q in entry.questions.all()))
        self.assertIn("first", entry.llm_response)

    def test_blank_answers_are_skipped(self):
        answer_questions(self.entry, {self.questions["cafe"].pk: "3", self.questions["dolor de cabeza"].pk: " "}, FakeClient())
        self.assertFalse(Question.objects.get(pk=self.questions["dolor de cabeza"].pk).is_answered)

    def test_second_round_merges_new_records(self):
        clarified = json.dumps({"records": [{"concept": "dolor de estomago", "kind": "symptom", "value": 8}]})
        entry = answer_questions(self.entry, {self.questions["cafe"].pk: "no sé"}, FakeClient(responses=[clarified]))
        names = set(entry.records.values_list("concept__name", flat=True))
        self.assertEqual(names, {"sueno", "cafe", "dolor de cabeza", "dolor de estomago"})

    def test_rejects_questions_from_other_entries(self):
        with self.assertRaises(ValueError):
            answer_questions(self.entry, {999_999: "3"}, FakeClient())

    def test_rejects_entries_without_pending_questions(self):
        answer_questions(self.entry, {self.questions["cafe"].pk: "3"}, FakeClient())
        with self.assertRaises(EntryStateError):
            answer_questions(self.entry, {self.questions["cafe"].pk: "4"}, FakeClient())

    def test_llm_failure_keeps_answers_and_allows_retry(self):
        with self.assertRaises(EntryProcessingError):
            answer_questions(self.entry, {self.questions["cafe"].pk: "3"}, FailingClient())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, Entry.Status.PENDING_QUESTIONS)
        self.assertEqual(Question.objects.get(pk=self.questions["cafe"].pk).answer, "3")
        self.assertIn("answers_error", self.entry.llm_response)
        self.assertIn("attempts", self.entry.llm_response)  # first extraction log kept

        entry = answer_questions(self.entry, {self.questions["cafe"].pk: "3"}, FakeClient())
        self.assertEqual(entry.status, Entry.Status.COMPLETE)
