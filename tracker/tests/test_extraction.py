import json
from datetime import date

from django.test import SimpleTestCase, TestCase, override_settings
from pydantic import ValidationError

from tracker.models import Concept, Entry, Record
from tracker.services.context import load_context
from tracker.services.extraction import (
    AnsweredQuestion,
    ConceptInfo,
    ExtractionContext,
    ExtractionError,
    RecentRecord,
    build_user_prompt,
    extract,
    parse_response,
)
from tracker.services.llm import FakeClient, GemmaClient, LLMError, get_llm_client

ENTRY_DATE = date(2026, 10, 16)

VALID_RESPONSE = json.dumps({
    "records": [
        {"concept": "sueno", "kind": "habit", "value": 5, "unit": "hours"},
        {"concept": "dolor de cabeza", "kind": "symptom", "value": None, "time_of_day": "afternoon"},
    ],
    "questions": [
        {"concept": "dolor de cabeza", "reason": "missing_intensity", "text": "¿Del 0 al 10?"},
    ],
})


class ParseResponseTests(SimpleTestCase):
    def test_accepts_code_fences_and_surrounding_text(self):
        raw = f"Here is the JSON:\n```json\n{VALID_RESPONSE}\n```\nDone."
        result = parse_response(raw)
        self.assertEqual([r.concept for r in result.records], ["sueno", "dolor de cabeza"])
        self.assertEqual(result.records[1].time_of_day, "afternoon")

    def test_empty_strings_become_defaults(self):
        raw = json.dumps({"records": [{"concept": "cafe", "kind": "habit", "unit": None, "time_of_day": ""}]})
        record = parse_response(raw).records[0]
        self.assertEqual(record.unit, "")
        self.assertIsNone(record.time_of_day)

    def test_rejects_intensity_out_of_scale(self):
        raw = json.dumps({"records": [{"concept": "estres", "kind": "state", "value": 15}]})
        with self.assertRaises(ValidationError):
            parse_response(raw)

    def test_rejects_unknown_kind(self):
        raw = json.dumps({"records": [{"concept": "cafe", "kind": "food"}]})
        with self.assertRaises(ValidationError):
            parse_response(raw)

    def test_keeps_at_most_three_questions(self):
        questions = [{"reason": "ambiguous", "text": f"Question {i}?"} for i in range(5)]
        result = parse_response(json.dumps({"records": [], "questions": questions}))
        self.assertEqual(len(result.questions), 3)

    def test_ambiguous_statement_creates_no_record(self):
        raw = json.dumps({
            "records": [
                {"concept": "Sentirse raro", "kind": "state"},
                {"concept": "cafe", "kind": "habit", "value": 1, "unit": "cups"},
            ],
            "questions": [
                {"concept": "sentirse raro", "reason": "ambiguous", "text": "¿Físico o emocional?"},
                {"concept": "sentirse raro", "reason": "missing_intensity", "text": "¿Del 0 al 10?"},
            ],
        })
        result = parse_response(raw)
        self.assertEqual([r.concept for r in result.records], ["cafe"])
        self.assertEqual(len(result.questions), 1)
        self.assertEqual(result.questions[0].reason, "ambiguous")
        self.assertIsNone(result.questions[0].concept)

    def test_without_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_response("I could not understand the note.")


class BuildUserPromptTests(SimpleTestCase):
    def test_includes_catalog_history_text_and_answers(self):
        context = ExtractionContext(
            concepts=[ConceptInfo("cafe", "habit", "cups", ("cafecito",))],
            recent_records=[RecentRecord(date(2026, 10, 15), "cafe", 2.0, "cups")],
        )
        prompt = build_user_prompt(
            "tomé café",
            ENTRY_DATE,
            context,
            answers=[AnsweredQuestion("cafe", "¿Cuántas tazas?", "3")],
        )
        self.assertIn("Entry date: 2026-10-16", prompt)
        self.assertIn("- cafe (habit; unit: cups; aliases: cafecito)", prompt)
        self.assertIn("- 2026-10-15 cafe: 2 cups", prompt)
        self.assertIn('"""\ntomé café\n"""', prompt)
        self.assertIn("- [cafe] ¿Cuántas tazas? -> 3", prompt)


class ExtractTests(SimpleTestCase):
    def test_returns_validated_result_on_first_attempt(self):
        client = FakeClient(responses=[VALID_RESPONSE])
        extraction = extract("dormí 5 horas", ENTRY_DATE, ExtractionContext(), client)
        self.assertEqual(extraction.attempts, 1)
        self.assertEqual(extraction.result.records[0].value, 5)

    def test_retries_once_with_error_feedback(self):
        client = FakeClient(responses=["not json at all", VALID_RESPONSE])
        extraction = extract("dormí 5 horas", ENTRY_DATE, ExtractionContext(), client)
        self.assertEqual(extraction.attempts, 2)
        self.assertEqual(len(extraction.durations), 2)
        retry_prompt = client.calls[1][1]
        self.assertIn("Your previous reply was not valid", retry_prompt)
        self.assertIn("not json at all", retry_prompt)

    def test_raises_after_max_attempts(self):
        client = FakeClient(responses=["nope", "still nope"])
        with self.assertRaises(ExtractionError) as caught:
            extract("dormí 5 horas", ENTRY_DATE, ExtractionContext(), client)
        self.assertEqual(caught.exception.raw_responses, ["nope", "still nope"])


class RuleBasedFakeClientTests(SimpleTestCase):
    def run_fake(self, text, answers=None, context=None):
        return extract(text, ENTRY_DATE, context or ExtractionContext(), FakeClient(), answers).result

    def test_extracts_quantities_and_asks_for_missing_values(self):
        result = self.run_fake("Dormí 5 horas, tomé café y me duele la cabeza desde la tarde")
        records = {r.concept: r for r in result.records}
        self.assertEqual(records["sueno"].value, 5)
        self.assertIsNone(records["cafe"].value)
        self.assertEqual(records["dolor de cabeza"].time_of_day, "afternoon")
        self.assertEqual(
            {q.reason for q in result.questions}, {"missing_quantity", "missing_intensity"}
        )

    def test_reads_intensity_words(self):
        cases = {"muy fuerte": 9, "fuerte": 8, "muy": 7, "leve": 3}
        for words, expected in cases.items():
            with self.subTest(words=words):
                result = self.run_fake(f"me dolió la cabeza {words}")
                self.assertEqual(result.records[0].value, expected)
                self.assertEqual(result.questions, [])

    def test_vague_statement_only_asks(self):
        result = self.run_fake("me sentí raro en la noche")
        self.assertEqual(result.records, [])
        self.assertEqual([q.reason for q in result.questions], ["ambiguous"])

    def test_answers_fill_values_and_stop_questions(self):
        result = self.run_fake("tomé café", answers=[AnsweredQuestion("cafe", "", "3 tazas")])
        self.assertEqual(result.records[0].value, 3)
        self.assertEqual(result.questions, [])

    def test_marks_unknown_concepts_as_new(self):
        context = ExtractionContext(concepts=[ConceptInfo("cafe", "habit", "cups")])
        result = self.run_fake("tomé 2 cafés y dormí 7 horas", context=context)
        self.assertEqual({r.concept: r.is_new for r in result.records}, {"cafe": False, "sueno": True})


class GetLLMClientTests(SimpleTestCase):
    @override_settings(LLM_PROVIDER="fake")
    def test_fake_provider(self):
        self.assertIsInstance(get_llm_client(), FakeClient)

    @override_settings(LLM_PROVIDER="gemma", GEMINI_API_KEY="")
    def test_gemma_without_key_fails_clearly(self):
        with self.assertRaisesMessage(LLMError, "GEMINI_API_KEY is empty"):
            get_llm_client()

    @override_settings(LLM_PROVIDER="other")
    def test_unknown_provider(self):
        with self.assertRaises(LLMError):
            get_llm_client()

    @override_settings(LLM_PROVIDER="gemma", GEMINI_API_KEY="test-key", LLM_THINKING_LEVEL="Minimal")
    def test_gemma_accepts_thinking_level(self):
        self.assertIsInstance(get_llm_client(), GemmaClient)

    def test_gemma_rejects_unknown_thinking_level(self):
        with self.assertRaisesMessage(LLMError, "Invalid thinking level"):
            GemmaClient(api_key="test-key", model="gemma", thinking_level="turbo")


class LoadContextTests(TestCase):
    def test_loads_catalog_and_only_the_last_three_days(self):
        coffee = Concept.objects.create(name="cafe", kind="habit", unit="cups", aliases=["cafecito"])
        for day, value in [(12, 1), (13, 2), (15, 3), (16, 4)]:
            entry = Entry.objects.create(date=date(2026, 10, day), raw_text="café")
            Record.objects.create(entry=entry, concept=coffee, value=value)

        context = load_context(ENTRY_DATE)

        self.assertEqual(context.concepts, [ConceptInfo("cafe", "habit", "cups", ("cafecito",))])
        self.assertEqual(
            [(r.date.day, r.value) for r in context.recent_records],
            [(13, 2.0), (15, 3.0)],
        )
