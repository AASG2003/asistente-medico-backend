from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from tracker.models import Concept, Entry, Record
from tracker.text import make_slug, normalize_name


class TextNormalizationTests(TestCase):
    def test_normalize_name_strips_accents_case_and_spaces(self):
        self.assertEqual(normalize_name("  Dolor de   CABÉZA "), "dolor de cabeza")

    def test_make_slug(self):
        self.assertEqual(make_slug("Café con leche"), "cafe-con-leche")


class ConceptTests(TestCase):
    def test_save_normalizes_name_slug_and_aliases(self):
        concept = Concept.objects.create(
            name="Dolor de Cabeza",
            kind=Concept.Kind.SYMPTOM,
            aliases=["Cefalea", "cefalea", "Migraña"],
        )
        self.assertEqual(concept.name, "dolor de cabeza")
        self.assertEqual(concept.slug, "dolor-de-cabeza")
        self.assertEqual(concept.aliases, ["cefalea", "migrana"])

    def test_slug_is_unique(self):
        Concept.objects.create(name="Café", kind=Concept.Kind.HABIT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Concept.objects.create(name="cafe", kind=Concept.Kind.HABIT)


class RecordTests(TestCase):
    def setUp(self):
        self.entry = Entry.objects.create(date=date(2026, 10, 1), raw_text="Tomé 2 cafés")
        self.coffee = Concept.objects.create(name="cafe", kind=Concept.Kind.HABIT, unit="cups")

    def test_record_copies_entry_date(self):
        record = Record.objects.create(entry=self.entry, concept=self.coffee, value=2)
        self.assertEqual(record.date, date(2026, 10, 1))

    def test_one_record_per_concept_and_entry(self):
        Record.objects.create(entry=self.entry, concept=self.coffee, value=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Record.objects.create(entry=self.entry, concept=self.coffee, value=1)
