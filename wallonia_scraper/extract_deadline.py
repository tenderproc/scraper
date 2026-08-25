"""Extract a real submission deadline from a Walloon council decision's text.

The structured `deadline` field this scraper writes to `external_opportunities`
is currently always None (see project memory) - not because these decisions
never state one, but because the scraper never tried to parse it out. A live
survey (2026-08-23) found 45+ records containing "date limite" and 19+
containing "remise des offres" with a real, explicit absolute date (e.g.
"la date limite de dépôt des offres est fixée au 30 juin 2026 à 12h00").

Real text is messy, so this is deliberately conservative - same "never
fabricate" discipline as extract.py's amount extraction:
- Only matches deadlines tied to actual bid submission ("offres"/
  "soumissionnaires"), never to unrelated deadlines that also use "date
  limite" in the same document (job-application deadlines - "candidatures" -
  administrative notification deadlines within an existing framework
  agreement, legal-opinion deadlines, completion/"achèvement" deadlines).
- Only matches an ABSOLUTE date. A relative-only deadline ("trente-cinq
  jours à compter de la date de l'envoi de l'avis de marché", "délai de 10
  jours ouvrables") is real but not resolvable to a concrete date without
  knowing the dispatch date - returns None rather than guessing.
- Each pattern is bounded to a single clause ([^.;]{0,N} - never crosses a
  ';' or '.') rather than a generic character-distance window. An earlier
  proximity-based version of this function grabbed the wrong date twice in
  manual verification against real records: once picking up an unrelated
  publication date from the PRECEDING clause when it happened to be closer
  than the actual (later, correct) rescheduled date, and once picking up a
  "délai légal minimal de remise des offres" reference in a later, unrelated
  clause that mentions the same phrase without actually stating a deadline
  for this decision. Clause-bounding rules out both failure modes structurally
  rather than by tuning a distance threshold.
- When multiple deadline-setting clauses exist in one document (a decision
  rescheduling an earlier one), the LAST match by text position wins -
  later text supersedes earlier text in how these documents are written.
"""
from __future__ import annotations

import re
from datetime import datetime, time as dt_time

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}

# The date/time we actually want to extract - always these exact group names.
_TARGET_DATE_TIME = (
    r"(?P<day>\d{1,2})\s+(?P<month>[a-zA-Zéûà]+)\s+(?P<year>\d{4})"
    r"(?:\s*(?:(?:(?:à|a)\s*)?(?P<hour>\d{1,2})[:h](?P<minute>\d{2})|(?:(?:à|a)\s*)?(?P<midi>midi)))?"
)
# A date to skip over (e.g. the superseded date in "du X au Y") - same shape,
# unnamed groups so it never collides with the target's named groups.
_OTHER_DATE = r"\d{1,2}\s+[a-zA-Zéûà]+\s+\d{4}"

_OFFER_WORD = r"(?:offres|soumissionnaires)"
_DEADLINE_NOUN = r"date\s+(?:limite|ultime)"
_SUBMIT_NOUN = r"(?:remise|dépôt|depot|introduction)"
_CLAUSE = r"[^.;]{0,90}?"  # never crosses a clause boundary

_PATTERNS = [
    # "...date limite d'introduction des offres du 13 novembre 2025 au 16
    # février 2026..." - a reschedule stated as a range; only the second
    # (superseding) date is the current deadline.
    re.compile(
        rf"{_DEADLINE_NOUN}{_CLAUSE}{_OFFER_WORD}{_CLAUSE}\bdu\s+{_OTHER_DATE}\s+au\s+{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # "la date limite ... offres ... est/était/sera fixée au 30 juin 2026 à
    # 12h00" / "...de fixer la date limite ... offres ... au ..."
    re.compile(
        rf"{_DEADLINE_NOUN}{_CLAUSE}{_OFFER_WORD}{_CLAUSE}fix(?:é|ée|er)[a-z]*\s+(?:au|à)\s+(?:\w+\s+)?{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # "la date de remise des offres était fixée au 2 mars 2026 à 11h00" /
    # "...a été reportée au 9 mars 2026..." / "...à savoir le 20 avril 2026..."
    re.compile(
        rf"{_SUBMIT_NOUN}\s+des\s+offres{_CLAUSE}(?:fix(?:é|ée)|report(?:é|ée)|à\s+savoir,?\s+le)\s*(?:au|à|le)?\s*(?:\w+\s+)?{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # "de fixer les date et heure de remise des offres au jeudi 05 septembre
    # 2024 à 10H" / "...fixer la remise au jeudi 19 septembre 2024..."
    re.compile(
        rf"fixer{_CLAUSE}{_SUBMIT_NOUN}{_CLAUSE}au\s+(?:\w+\s+)?{_TARGET_DATE_TIME}",
        re.IGNORECASE,
    ),
    # Backward construction: "la date du 29 mai 2026 à 10h00 est proposée
    # comme date limite d'introduction des offres"
    re.compile(
        rf"la\s+date\s+du\s+{_TARGET_DATE_TIME}{_CLAUSE}(?:est|était)\s+proposée\s+comme\s+{_DEADLINE_NOUN}",
        re.IGNORECASE,
    ),
]


def _parse(m: re.Match) -> datetime | None:
    month = _FR_MONTHS.get(m.group("month").lower())
    if not month:
        return None
    try:
        d = datetime(int(m.group("year")), month, int(m.group("day")))
    except ValueError:
        return None
    if m.group("midi"):
        return datetime.combine(d.date(), dt_time(12, 0))
    hour, minute = m.group("hour"), m.group("minute")
    if hour is not None and minute is not None:
        try:
            return datetime.combine(d.date(), dt_time(int(hour), int(minute)))
        except ValueError:
            return d
    return d


def extract_deadline(text: str | None) -> str | None:
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
