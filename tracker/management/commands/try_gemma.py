"""
Runs an extraction from the terminal without saving anything.

    python manage.py try_gemma "dormí 5 horas y me duele la cabeza desde la tarde"
    python manage.py try_gemma "tomé café" --provider fake --show-prompt
    python manage.py try_gemma "tomé café" --answer "cafe=3"
    python manage.py try_gemma "tomé café" --thinking-level minimal
"""

import json
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker.services.context import load_context
from tracker.services.extraction import SYSTEM_PROMPT, AnsweredQuestion, ExtractionError, build_user_prompt, extract
from tracker.services.llm import THINKING_LEVELS, FakeClient, GemmaClient, LLMError


class Command(BaseCommand):
    help = "Extracts structured records from a text with the configured LLM (nothing is saved)."

    def add_arguments(self, parser):
        parser.add_argument("text", help="Free text, e.g. 'dormí 5 horas y tomé 2 cafés'.")
        parser.add_argument(
            "--date", type=date.fromisoformat, default=timezone.localdate(), help="Entry date (YYYY-MM-DD)."
        )
        parser.add_argument("--provider", choices=["gemma", "fake"], default=settings.LLM_PROVIDER)
        parser.add_argument(
            "--answer",
            action="append",
            default=[],
            metavar="CONCEPT=ANSWER",
            help="Simulate a follow-up answer. Can be repeated.",
        )
        parser.add_argument(
            "--thinking-level",
            choices=["default", *THINKING_LEVELS],
            default=settings.LLM_THINKING_LEVEL or "default",
            help="Override LLM_THINKING_LEVEL to compare speed and quality.",
        )
        parser.add_argument("--show-prompt", action="store_true", help="Print the prompts sent to the model.")

    def handle(self, *args, **options):
        answers = [self._parse_answer(value) for value in options["answer"]]
        context = load_context(options["date"])

        if options["show_prompt"]:
            self.stdout.write(self.style.MIGRATE_HEADING("System prompt"))
            self.stdout.write(SYSTEM_PROMPT)
            self.stdout.write(self.style.MIGRATE_HEADING("User prompt"))
            self.stdout.write(build_user_prompt(options["text"], options["date"], context, answers))

        thinking_level = "" if options["thinking_level"] == "default" else options["thinking_level"]
        try:
            client = (
                GemmaClient(
                    api_key=settings.GEMINI_API_KEY,
                    model=settings.LLM_MODEL,
                    thinking_level=thinking_level,
                )
                if options["provider"] == "gemma"
                else FakeClient()
            )
            extraction = extract(options["text"], options["date"], context, client, answers)
        except LLMError as error:
            raise CommandError(str(error)) from error
        except ExtractionError as error:
            for raw in error.raw_responses:
                self.stderr.write(raw)
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Raw response ({options['provider']}, attempt {extraction.attempts})"
        ))
        self.stdout.write(extraction.raw_response)
        self.stdout.write(self.style.MIGRATE_HEADING("Validated result"))
        self.stdout.write(json.dumps(extraction.result.model_dump(), indent=2, ensure_ascii=False))

        self.stdout.write(self.style.MIGRATE_HEADING("Timing"))
        if options["provider"] == "gemma":
            self.stdout.write(f"Model: {settings.LLM_MODEL}, thinking level: {options['thinking_level']}")
        for attempt, seconds in enumerate(extraction.durations, start=1):
            self.stdout.write(f"Attempt {attempt}: {seconds:.2f} s")
        self.stdout.write(f"Total: {sum(extraction.durations):.2f} s")

        usage = getattr(client, "last_usage", None)
        if usage:
            self.stdout.write(
                "Tokens (last call): "
                + ", ".join(f"{name}={count if count is not None else 0}" for name, count in usage.items())
            )

    @staticmethod
    def _parse_answer(value: str) -> AnsweredQuestion:
        concept, separator, answer = value.partition("=")
        if not separator or not answer.strip():
            raise CommandError(f"Invalid --answer {value!r}, expected CONCEPT=ANSWER.")
        return AnsweredQuestion(concept=concept.strip(), text="", answer=answer.strip())
