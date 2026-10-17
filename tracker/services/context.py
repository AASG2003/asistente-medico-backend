"""Builds the extraction context (known concepts + recent history) from the database."""

from datetime import date, timedelta

from ..models import Concept, Record
from .extraction import ConceptInfo, ExtractionContext, RecentRecord

RECENT_DAYS = 3


def load_context(entry_date: date, recent_days: int = RECENT_DAYS) -> ExtractionContext:
    concepts = [
        ConceptInfo(name=c.name, kind=c.kind, unit=c.unit, aliases=tuple(c.aliases))
        for c in Concept.objects.all()
    ]
    records = (
        Record.objects.select_related("concept")
        .filter(date__gte=entry_date - timedelta(days=recent_days), date__lt=entry_date)
        .order_by("date", "concept__name")
    )
    recent = [
        RecentRecord(
            date=r.date,
            concept=r.concept.name,
            value=None if r.value is None else float(r.value),
            unit=r.concept.unit,
        )
        for r in records
    ]
    return ExtractionContext(concepts=concepts, recent_records=recent)
