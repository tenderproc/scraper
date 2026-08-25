"""Dutch counterpart to extract_deadline.py, for Flanders Gelinkt Notuleren
council-decision text.

Survey (2026-08-23) found the dominant Flemish phrasing is "De offertes
dienen het bestuur ten laatste te bereiken op <date>" (the offers must
reach the authority at the latest on <date>) - but confirmed against the
full table that the overwhelming majority of matches (36/48 direct, more
once combined with the sibling "uitgenodigd" marker fix) are closed
negotiated-without-publication or accepted-invoice ("aanvaarde factuur")
procedures with a named-count invited shortlist, already excluded by
NON_TENDER_MARKERS in lib/externalOpportunities.ts - extracting their
deadline is still honest/useful data (same as Wallonia's approach, where
extraction is independent of the open/closed marker classification) but
won't by itself surface new rows. Only ~2-3 records in the full table were
genuinely open procedures with an extractable deadline.

Same conservative, clause-bounded, never-fabricate discipline as
extract_deadline.py - see that module's docstring for the full rationale.
"""
from __future__ import annotations

import re
from datetime import datetime, time as dt_time

_NL_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}

_TARGET_DATE_TIME = (
    r"(?P<day>\d{1,2})\s+(?P<month>[a-zA-Z]+)\s+(?P<year>\d{4})"
    r"(?:\s*om\s*(?:(?P<hour>\d{1,2})[u:.](?P<minute>\d{2})\s*(?:uur)?))?"
)
_CLAUSE = r"[^.;]{0,90}?"

_PATTERNS = [
    # "De offertes dienen het bestuur ten laatste te bereiken op 30 juni
    # 2026 om 12.00 uur." / "...offertes het bestuur moeten bereiken ten
    # laatste op..." / "...offertes het bestuur ten laatste dienen te
    # bereiken op..." (word-order variants all seen live)
    re.compile(
        rf"offertes{_CLAUSE}(?:ten laatste{_CLAUSE}bereiken|bereiken{_CLAUSE}ten laatste)\s+op\s+{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # "limietdatum voor het indienen van de offertes ... tot 21 augustus
    # 2026 om 10u00" (a deadline extension)
    re.compile(
        rf"limietdatum{_CLAUSE}offertes{_CLAUSE}\btot\s+{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # "De offertes dienen vóór 6 november 2024 om 12.00 uur ingediend te
    # worden"
    re.compile(
        rf"offertes\s+dienen\s+vóór\s+{_TARGET_DATE_TIME}{_CLAUSE}ingediend",
        re.IGNORECASE,
    ),
]


def _parse(m: re.Match) -> datetime | None:
    month = _NL_MONTHS.get(m.group("month").lower())
    if not month:
        return None
    try:
        d = datetime(int(m.group("year")), month, int(m.group("day")))
    except ValueError:
        return None
    hour, minute = m.group("hour"), m.group("minute")
    if hour is not None and minute is not None:
        try:
            return datetime.combine(d.date(), dt_time(int(hour), int(minute)))
        except ValueError:
            return d
    return d


def extract_deadline_nl(text: str | None) -> str | None:
    """Return an ISO datetime string for the submission deadline, or None if
    no absolute, bid-specific deadline was found."""
    if not text:
        return None

    best: datetime | None = None
    best_pos = -1
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            parsed = _parse(m)
            if parsed is None:
                continue
            if m.start() > best_pos:
                best = parsed
                best_pos = m.start()

    return best.isoformat() if best else None
