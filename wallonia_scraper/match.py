"""Match a Wallonia deliberations.be decision against BOSA's ingested data.

The first full-scale dedup pass (2026-08-20) found real confirmed
duplicates only at a 0.5+ raw SequenceMatcher score on full titles, and
even those scored barely above that (0.51-0.667) because Wallonia titles
routinely carry a leading department/category label BOSA titles never
have - e.g. "Finances - Marché public de fournitures - Acquisition d'un
véhicule..." vs BOSA's bare "Acquisition d'un véhicule...". That prefix
noise both suppresses the score of genuine matches and makes a single
fixed threshold unreliable. This module fixes the comparison itself
instead of just tuning the threshold: strip the label, compare on
significant word tokens (order- and prefix-insensitive) rather than raw
character sequences, and treat a close amount match as strong
corroborating evidence.
"""
from __future__ import annotations

import re
import unicodedata

# Trying to pattern-match French department-label *grammar* (see git
# history) turned out too fragile - labels routinely include ordinary
# lowercase connector words ("Pôle Cadre de vie", "Service Mobilité") that
# a capitalization-based regex can't distinguish from the start of a real
# sentence. Simpler and more robust: department labels are short (a handful
# of words) and Wallonia titles often stack 1-3 of them before the actual
# decision object, so cut at the LAST dash/colon separator found within a
# short leading window - real sentence content essentially never contains
# a bare " - " that early.
_SEPARATOR_RE = re.compile(r"\s[-:–]\s")
_LABEL_WINDOW_CHARS = 80


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


_STOPWORDS = {
    "le", "la", "les", "l", "de", "des", "du", "un", "une", "et", "a", "au", "aux",
    "pour", "dans", "sur", "avec", "par", "en", "ce", "cette", "ces", "son", "sa",
    "ses", "qui", "que", "d", "n", "ou", "se", "sont", "est", "ete", "etre",
}


def strip_label_prefix(title: str) -> str:
    """Cut at the last " - "/" : " separator within the first
    _LABEL_WINDOW_CHARS characters, dropping any department-label segments
    stacked before the real decision text (e.g. "Pôle Cadre de vie -
    Service Mobilité - Marché public de travaux - Poursuite des travaux de
    réfection..." -> "Poursuite des travaux de réfection..."). If no
    separator falls within that window, the title is returned unchanged -
    most titles have no label prefix at all, and this must not eat into
    genuine sentence content just because it happens to contain a dash."""
    window = title[:_LABEL_WINDOW_CHARS]
    last_match = None
    for m in _SEPARATOR_RE.finditer(window):
        last_match = m
    if last_match is None:
        return title
    return title[last_match.end():]


def _tokens(text: str) -> set[str]:
    folded = _fold(text)
    words = re.findall(r"[a-z0-9]+", folded)
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def token_similarity(a: str, b: str) -> float:
    """Jaccard similarity on significant word tokens - robust to prefix
    noise, word reordering, and minor phrasing differences in a way raw
    character-sequence similarity is not."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def amounts_close(a: float | None, b: float | None, rel_tol: float = 0.03) -> bool:
    """True if both amounts are present and within rel_tol of each other -
    a strong corroborating signal that two records describe the same
    tender, since exact contract values rarely coincide by chance."""
    if a is None or b is None or a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= rel_tol


def match_score(wallonia_title: str, bosa_title: str, wallonia_amount: float | None, bosa_amount: float | None) -> float:
    """Combined confidence score in [0, 1] that a Wallonia decision and a
    BOSA record describe the same underlying tender. Token similarity on
    the label-stripped title is the base signal; a close amount match adds
    a fixed bonus since it's independent, strong evidence."""
    base = token_similarity(strip_label_prefix(wallonia_title), bosa_title)
    if amounts_close(wallonia_amount, bosa_amount):
        base = min(1.0, base + 0.3)
    return base
