"""
LLM clients behind one small interface.

The rest of the code only depends on `LLMClient.generate()`, so the real model
can be swapped for a fake one in tests or when there is no network.
"""

import json
import re
from typing import Protocol

from django.conf import settings


class LLMError(Exception):
    """The LLM could not be reached or returned no text."""


class LLMClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...


# Gemma on the Gemini API only accepts these two; "low" and "medium" return HTTP 400.
THINKING_LEVELS = ("minimal", "high")


class GemmaClient:
    """Calls Gemma through the Gemini API (google-genai SDK)."""

    def __init__(self, api_key: str, model: str, thinking_level: str = "", timeout_seconds: int = 60):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is empty. Set it in .env or use LLM_PROVIDER=fake.")
        thinking_level = thinking_level.strip().lower()
        if thinking_level and thinking_level not in THINKING_LEVELS:
            raise LLMError(f"Invalid thinking level {thinking_level!r}. Use one of {', '.join(THINKING_LEVELS)}.")
        # Imported here so the fake client works even without the SDK configured.
        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        # Empty means "model default". Lower levels answer faster but reason less.
        self._thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
        # Token counts of the last call, to compare thinking levels.
        self.last_usage: dict[str, int | None] | None = None

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=system_prompt,
            # Low temperature: we want consistent extraction, not creativity.
            temperature=0.1,
            thinking_config=(
                self._types.ThinkingConfig(thinking_level=self._thinking_level.upper())
                if self._thinking_level
                else None
            ),
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=config,
            )
        except errors.APIError as error:
            raise LLMError(f"Gemini API error {error.code}: {error.message}") from error
        except Exception as error:  # network errors, timeouts
            raise LLMError(f"Could not reach the Gemini API: {error}") from error

        usage = response.usage_metadata
        self.last_usage = usage and {
            "prompt": usage.prompt_token_count,
            "thoughts": usage.thoughts_token_count,
            "output": usage.candidates_token_count,
            "total": usage.total_token_count,
        }
        if not response.text:
            raise LLMError("The model returned an empty response.")
        return response.text


class FakeClient:
    """
    Offline stand-in for Gemma.

    - With `responses`, it returns them in order (useful for tests).
    - Without them, it applies a few keyword rules on the user text, enough to
      demo the full flow without an API key.
    """

    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self._responses:
            return self._responses.pop(0)
        return json.dumps(_rule_based_extraction(user_prompt), ensure_ascii=False)


def get_llm_client() -> LLMClient:
    """Builds the client configured in settings (LLM_PROVIDER)."""
    provider = settings.LLM_PROVIDER
    if provider == "gemma":
        return GemmaClient(
            api_key=settings.GEMINI_API_KEY,
            model=settings.LLM_MODEL,
            thinking_level=settings.LLM_THINKING_LEVEL,
        )
    if provider == "fake":
        return FakeClient()
    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r}. Use 'gemma' or 'fake'.")


# Rule-based fake --------------------------------------------------------------

_USER_TEXT = re.compile(r'User text:\s*"""(.*?)"""', re.DOTALL)
_ANSWER = re.compile(r"^- \[(?P<concept>[^\]]+)\].*?->\s*(?P<answer>.+)$", re.MULTILINE)
_NUMBER = r"(\d+(?:[.,]\d+)?)"

_TIME_OF_DAY = {
    "morning": ("manana", "mañana"),
    "afternoon": ("tarde",),
    "night": ("noche",),
}
# Checked in order, so "muy fuerte" wins over "fuerte" and "muy".
_INTENSITY_WORDS = [
    ("insoportable", 10), ("muy fuerte", 9), ("fuerte", 8), ("moderad", 5),
    ("leve", 3), ("un poco", 2), ("muy", 7),
]


def _to_number(text: str) -> float | None:
    match = re.search(_NUMBER, text)
    return float(match.group(1).replace(",", ".")) if match else None


def _time_of_day(text: str) -> str | None:
    for value, words in _TIME_OF_DAY.items():
        if any(word in text for word in words):
            return value
    return None


def _rule_based_extraction(user_prompt: str) -> dict:
    match = _USER_TEXT.search(user_prompt)
    text = (match.group(1) if match else user_prompt).lower()
    answers = {m["concept"].strip(): m["answer"].strip() for m in _ANSWER.finditer(user_prompt)}
    records, questions = [], []

    def add(concept, kind, unit, value, question_reason, question_text, context=""):
        if value is None and concept in answers:
            value = _to_number(answers[concept])
        records.append({
            "concept": concept,
            "is_new": f"\n- {concept} (" not in user_prompt,
            "kind": kind,
            "value": value,
            "unit": unit,
            "time_of_day": _time_of_day(context),
        })
        if value is None and concept not in answers:
            questions.append({"concept": concept, "reason": question_reason, "text": question_text})

    sleep = re.search(rf"dorm\w*(?:\s+\w+)?\s+{_NUMBER}\s*h", text)
    if sleep or "dorm" in text:
        add("sueno", "habit", "hours", _to_number(sleep.group(0)) if sleep else None,
            "missing_quantity", "¿Cuántas horas dormiste?")

    coffee = re.search(rf"{_NUMBER}\s*(?:tazas? de\s+)?caf", text)
    if coffee or "caf" in text:
        add("cafe", "habit", "cups", _to_number(coffee.group(0)) if coffee else None,
            "missing_quantity", "¿Cuántas tazas de café tomaste?")

    screen = re.search(rf"(celular|computadora)\D{{0,20}}?{_NUMBER}\s*h", text) or re.search(
        rf"{_NUMBER}\s*h\w*\s+(?:en|con|de)\s+(?:el|la)\s+(celular|computadora)", text
    )
    if screen:
        device = next(group for group in screen.groups() if not re.fullmatch(_NUMBER, group))
        add(f"uso de {device}", "habit", "hours", _to_number(screen.group(0)),
            "missing_quantity", f"¿Cuántas horas usaste el {device}?")

    headache = re.search(r"(dolor de cabeza|(?:duele|doli\w*) la cabeza|cefalea)(.{0,40})", text)
    if headache:
        tail = headache.group(2)
        intensity = next((v for word, v in _INTENSITY_WORDS if word in tail), None)
        add("dolor de cabeza", "symptom", "", intensity,
            "missing_intensity", "Del 0 al 10, ¿qué tan fuerte fue el dolor de cabeza?", tail)

    if "raro" in text and "general" not in answers:
        # Ambiguous: no record until the user clarifies.
        questions.append({
            "concept": None,
            "reason": "ambiguous",
            "text": "¿A qué te refieres con sentirte raro? ¿Es algo físico o emocional?",
        })

    return {"records": records, "questions": questions[:3]}
