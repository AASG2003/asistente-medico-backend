"""
Turns free text into structured records using an LLM.

This module has no database access: it receives a plain `ExtractionContext`
and returns a validated `ExtractionResult`, so it is easy to test.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..text import normalize_name
from .llm import LLMClient

MAX_QUESTIONS = 3
INTENSITY_MAX = 10


# Output schema -----------------------------------------------------------------

Kind = Literal["habit", "symptom", "state"]
TimeOfDay = Literal["morning", "afternoon", "night"]
QuestionReason = Literal["missing_quantity", "missing_intensity", "ambiguous"]


class ExtractedRecord(BaseModel):
    concept: str = Field(min_length=1, max_length=100)
    is_new: bool = False
    kind: Kind
    value: float | None = Field(default=None, ge=0)
    unit: str = ""
    time_of_day: TimeOfDay | None = None

    @field_validator("unit", mode="before")
    @classmethod
    def empty_unit(cls, value):
        return value or ""

    @field_validator("time_of_day", mode="before")
    @classmethod
    def empty_time_of_day(cls, value):
        return value or None

    @model_validator(mode="after")
    def intensity_in_scale(self):
        if self.kind != "habit" and self.value is not None and self.value > INTENSITY_MAX:
            raise ValueError(f"{self.concept}: intensity must be between 0 and {INTENSITY_MAX}")
        return self


class ExtractedQuestion(BaseModel):
    concept: str | None = None
    reason: QuestionReason
    text: str = Field(min_length=3, max_length=255)


class ExtractionResult(BaseModel):
    records: list[ExtractedRecord] = Field(default_factory=list)
    questions: list[ExtractedQuestion] = Field(default_factory=list)

    @field_validator("questions", mode="before")
    @classmethod
    def limit_questions(cls, value):
        return value[:MAX_QUESTIONS] if isinstance(value, list) else value

    @model_validator(mode="after")
    def withhold_ambiguous(self):
        """
        A vague statement must not become a concept until the user clarifies it,
        otherwise the catalog fills with things like "sentirse raro". The prompt
        already asks for this; here we enforce it in case the model ignores it.
        """
        ambiguous = {
            normalize_name(q.concept) for q in self.questions if q.reason == "ambiguous" and q.concept
        }
        if not ambiguous:
            return self
        self.records = [r for r in self.records if normalize_name(r.concept) not in ambiguous]
        questions = []
        for question in self.questions:
            if question.concept and normalize_name(question.concept) in ambiguous:
                if question.reason != "ambiguous":
                    continue  # e.g. "how intense was it?" makes no sense yet
                question = question.model_copy(update={"concept": None})
            questions.append(question)
        self.questions = questions
        return self


# Input context -----------------------------------------------------------------


@dataclass(frozen=True)
class ConceptInfo:
    name: str
    kind: str
    unit: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecentRecord:
    date: date
    concept: str
    value: float | None
    unit: str = ""


@dataclass(frozen=True)
class AnsweredQuestion:
    concept: str | None
    text: str
    answer: str


@dataclass(frozen=True)
class ExtractionContext:
    concepts: list[ConceptInfo] = field(default_factory=list)
    recent_records: list[RecentRecord] = field(default_factory=list)


@dataclass(frozen=True)
class Extraction:
    result: ExtractionResult
    raw_response: str
    attempts: int
    # Seconds spent waiting for the LLM, one value per attempt.
    durations: tuple[float, ...] = ()


class ExtractionError(Exception):
    """The LLM answered, but never with valid JSON for our schema."""

    def __init__(self, message: str, raw_responses: list[str]):
        super().__init__(message)
        self.raw_responses = raw_responses


# Prompts -------------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
You extract structured health-tracking data from short personal notes.
The notes are usually written in Spanish. Reply with JSON only, no prose, no markdown.

Each fact in the note becomes one record:
- kind "habit": an action or behavior with a quantity (sleep, coffee, exercise, water,
  phone or computer use, alcohol, meals, cigarettes, medication).
- kind "symptom": a physical complaint (headache, nausea, back pain).
- kind "state": a mental or emotional state (stress, anxiety, low mood, energy).

When the note says one thing caused another ("por usar el celular 5 horas me dolio la cabeza"),
record BOTH as separate records: the cause (usually a habit, with its quantity) and the effect.
Never drop a fact because it is mentioned as an explanation; relations are analyzed later.

Rules:
1. Reuse a name from "Known concepts" (or one of its aliases) whenever the meaning matches.
   Use the canonical name, not the alias. Only if nothing matches, invent a short,
   lowercase name in the language of the note and set "is_new": true.
   Name what the person did or felt, using their own everyday word, not a component,
   cause or category: "cafe" (not "cafeina"), "sueno" (not "descanso"), "dolor de cabeza".
2. Habits: "value" is the quantity in the concept unit (convert, e.g. "media hora" -> 30 minutes).
   Every habit needs a unit, even when the value is null: the natural English one
   ("hours", "cups", "glasses", "minutes", "times"). Symptoms and states use "unit": "".
   Symptoms and states: "value" is intensity from 0 to {INTENSITY_MAX}
   (un poco ~2, leve ~3, moderado ~5, muy ~7, fuerte ~8, muy fuerte ~9, insoportable ~10).
   "muy" alone ("muy estresado", "muy cansado") counts as ~7, so do not ask for it.
3. If the quantity or intensity is not stated, use "value": null. Never guess numbers.
4. "time_of_day" is "morning", "afternoon", "night" or null.
5. Ask a question only when an answer would change a value or resolve an ambiguity:
   - "missing_quantity": a habit without a quantity.
   - "missing_intensity": a symptom or state without an intensity.
   - "ambiguous": a vague statement ("me senti raro") that could be several concepts.
     Do NOT create a record for it: ask one "ambiguous" question with "concept": null
     and create the record only after the follow-up answer clarifies it.
   At most {MAX_QUESTIONS} questions, short, in the language of the note.
   Use "Recent records" to resolve references like "otra vez" or "lo mismo de ayer".
6. If follow-up answers are given, use them to fill values and do not ask again.

JSON format:
{{
  "records": [
    {{"concept": "cafe", "is_new": false, "kind": "habit", "value": 2, "unit": "cups", "time_of_day": "morning"}},
    {{"concept": "dolor de cabeza", "is_new": false, "kind": "symptom", "value": null, "unit": "", "time_of_day": "afternoon"}}
  ],
  "questions": [
    {{"concept": "dolor de cabeza", "reason": "missing_intensity", "text": "Del 0 al 10, ¿que tan fuerte fue el dolor?"}}
  ]
}}
"""


def _format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "(no value)"
    number = f"{value:g}"
    return f"{number} {unit}".strip()


def build_user_prompt(
    text: str,
    entry_date: date,
    context: ExtractionContext,
    answers: list[AnsweredQuestion] | None = None,
) -> str:
    lines = [f"Entry date: {entry_date.isoformat()}", "", "Known concepts:"]
    if context.concepts:
        for concept in context.concepts:
            details = [concept.kind]
            if concept.unit:
                details.append(f"unit: {concept.unit}")
            if concept.aliases:
                details.append(f"aliases: {', '.join(concept.aliases)}")
            lines.append(f"- {concept.name} ({'; '.join(details)})")
    else:
        lines.append("- (none yet)")

    lines += ["", "Recent records:"]
    if context.recent_records:
        for record in context.recent_records:
            lines.append(f"- {record.date.isoformat()} {record.concept}: {_format_value(record.value, record.unit)}")
    else:
        lines.append("- (none)")

    lines += ["", 'User text:', '"""', text.strip(), '"""']

    if answers:
        lines += ["", "Follow-up answers:"]
        for answer in answers:
            question = f" {answer.text}" if answer.text else ""
            lines.append(f"- [{answer.concept or 'general'}]{question} -> {answer.answer}")

    return "\n".join(lines)


# Parsing -------------------------------------------------------------------------

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_response(raw: str) -> ExtractionResult:
    """Extracts the JSON object from the model output and validates it."""
    cleaned = _CODE_FENCE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in the response.")
    data = json.loads(cleaned[start : end + 1])
    return ExtractionResult.model_validate(data)


def extract(
    text: str,
    entry_date: date,
    context: ExtractionContext,
    client: LLMClient,
    answers: list[AnsweredQuestion] | None = None,
    max_attempts: int = 2,
) -> Extraction:
    """
    Asks the LLM for a structured extraction. If the output is not valid, it
    retries once telling the model what was wrong.
    """
    user_prompt = build_user_prompt(text, entry_date, context, answers)
    prompt = user_prompt
    raw_responses: list[str] = []
    durations: list[float] = []

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        raw = client.generate(SYSTEM_PROMPT, prompt)
        durations.append(time.perf_counter() - started)
        raw_responses.append(raw)
        try:
            return Extraction(
                result=parse_response(raw),
                raw_response=raw,
                attempts=attempt,
                durations=tuple(durations),
            )
        except (ValueError, ValidationError) as error:
            # json.JSONDecodeError is a ValueError too.
            prompt = (
                f"{user_prompt}\n\n"
                f"Your previous reply was not valid:\n{raw}\n\n"
                f"Error: {error}\n"
                "Reply again with only the corrected JSON."
            )

    raise ExtractionError(
        f"The model did not return valid JSON after {max_attempts} attempts.",
        raw_responses,
    )
