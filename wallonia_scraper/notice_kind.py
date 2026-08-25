"""Classify a scraped record as a genuine open call ("open"), an
already-decided purchase ("awarded"), or ambiguous ("unclear") — the same
distinction TED's own `isOpenCallNotice()` makes for `cn-*`/`can-*` notice
types, needed here because a large share of council-decision text describes
something already decided (a framework call-off, a negotiated purchase with
no public competition) rather than something a business could still bid on.

Reuses the phrase markers already validated in dedup_check.py rather than
inventing a second classification vocabulary. "awarded" is checked first:
a framework call-off is award-shaped even if it happens to also mention
"avis de marché" in a citation of the original (someone else's) framework.
"""
from __future__ import annotations

import unicodedata

from .dedup_check import (
    _EU_THRESHOLD_MARKERS,
    _FRAMEWORK_CALLOFF_MARKERS,
    _SUB_THRESHOLD_MARKERS,
    _WILL_BE_PUBLISHED_MARKERS,
)


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def classify_notice_kind(title: str | None, description: str | None, matiere: str | None = None) -> str:
    text = _fold(f"{title or ''} {description or ''}")

    if any(m in text for m in _FRAMEWORK_CALLOFF_MARKERS):
        return "awarded"
    if any(m in text for m in _SUB_THRESHOLD_MARKERS):
        # "sans publication prealable" / "marche de faible montant" means no
        # public competition happened at all - not something to bid on.
        return "awarded"
    if any(m in text for m in _WILL_BE_PUBLISHED_MARKERS) or any(m in text for m in _EU_THRESHOLD_MARKERS):
        return "open"
    if matiere and _fold(matiere) in ("marches publics", "marches public"):
        # conseilcommunal.be's own category, with no awarded marker present.
        return "open"
    return "unclear"
