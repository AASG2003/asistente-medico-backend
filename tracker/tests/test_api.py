from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from tracker.models import Concept, Entry

FROM_TEXT_URL = "/api/entries/from-text/"


@override_settings(LLM_PROVIDER="fake")
class EntryApiTests(APITestCase):
    def post_text(self, text, **extra):
        return self.client.post(FROM_TEXT_URL, {"text": text, **extra}, format="json")

    def test_from_text_returns_entry_with_records_and_questions(self):
        response = self.post_text("Dormí 5 horas y tomé café", date="2026-10-16")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["status"], "pending_questions")
        self.assertEqual(body["date"], "2026-10-16")
        sleep = next(r for r in body["records"] if r["concept_name"] == "sueno")
        self.assertEqual((sleep["kind"], sleep["unit"], sleep["value"]), ("habit", "hours", "5.00"))
        self.assertEqual(body["questions"][0]["reason"], "missing_quantity")

    def test_from_text_defaults_to_today(self):
        response = self.post_text("Dormí 7 horas")
        self.assertEqual(response.json()["date"], timezone.localdate().isoformat())

    def test_from_text_validates_input(self):
        self.assertEqual(self.post_text("").status_code, status.HTTP_400_BAD_REQUEST)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.post_text("Dormí 7 horas", date=tomorrow)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date", response.json())

    @override_settings(LLM_PROVIDER="gemma", GEMINI_API_KEY="")
    def test_from_text_llm_failure_returns_502_and_keeps_entry(self):
        response = self.post_text("Dormí 5 horas")
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.json()["entry"]["status"], "error")
        self.assertEqual(Entry.objects.get().raw_text, "Dormí 5 horas")

    def test_full_flow_text_questions_answers(self):
        entry = self.post_text("Tomé café y me duele la cabeza").json()
        questions = {q["concept_name"]: q["id"] for q in entry["questions"]}

        response = self.client.post(
            f"/api/entries/{entry['id']}/answer/",
            {"answers": [
                {"question": questions["cafe"], "answer": "2 tazas"},
                {"question": questions["dolor de cabeza"], "answer": "6"},
            ]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(
            {r["concept_name"]: r["value"] for r in body["records"]},
            {"cafe": "2.00", "dolor de cabeza": "6.00"},
        )
        self.assertTrue(all(q["answered_at"] for q in body["questions"]))

    def test_answer_errors(self):
        entry = self.post_text("Tomé café").json()
        url = f"/api/entries/{entry['id']}/answer/"
        question_id = entry["questions"][0]["id"]

        empty = self.client.post(url, {"answers": []}, format="json")
        self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)

        unknown = self.client.post(url, {"answers": [{"question": 999999, "answer": "1"}]}, format="json")
        self.assertEqual(unknown.status_code, status.HTTP_400_BAD_REQUEST)

        ok = self.client.post(url, {"answers": [{"question": question_id, "answer": "1"}]}, format="json")
        self.assertEqual(ok.status_code, status.HTTP_200_OK)

        again = self.client.post(url, {"answers": [{"question": question_id, "answer": "2"}]}, format="json")
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

    def test_list_filters_and_pagination(self):
        self.post_text("Dormí 7 horas", date="2026-10-10")
        self.post_text("Tomé café", date="2026-10-15")

        body = self.client.get("/api/entries/").json()
        self.assertEqual(body["count"], 2)

        filtered = self.client.get("/api/entries/?date_from=2026-10-12&status=pending_questions").json()
        self.assertEqual([e["date"] for e in filtered["results"]], ["2026-10-15"])

        invalid = self.client.get("/api/entries/?date_from=yesterday")
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_entry_removes_its_records(self):
        entry = self.post_text("Dormí 7 horas").json()
        response = self.client.delete(f"/api/entries/{entry['id']}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Entry.objects.exists())
        self.assertTrue(Concept.objects.filter(slug="sueno").exists())  # catalog is kept


@override_settings(LLM_PROVIDER="fake")
class ConceptApiTests(APITestCase):
    def test_list_filter_and_review(self):
        self.client.post(FROM_TEXT_URL, {"text": "Dormí 7 horas y me duele la cabeza fuerte"}, format="json")

        habits = self.client.get("/api/concepts/?kind=habit").json()["results"]
        self.assertEqual([c["name"] for c in habits], ["sueno"])

        pending = self.client.get("/api/concepts/?reviewed=false").json()
        self.assertEqual(pending["count"], 2)

        response = self.client.patch(
            f"/api/concepts/{habits[0]['id']}/", {"reviewed": True, "aliases": ["Descanso"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["aliases"], ["descanso"])

    def test_concepts_cannot_be_deleted(self):
        concept = Concept.objects.create(name="agua", kind="habit", unit="glasses")
        response = self.client.delete(f"/api/concepts/{concept.pk}/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
