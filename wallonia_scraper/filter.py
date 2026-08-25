"""Procurement-keyword filtering, accent-insensitive."""
from __future__ import annotations

import unicodedata

from . import config


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


_FOLDED_KEYWORDS = [(_fold(kw), kw) for kw in config.PROCUREMENT_KEYWORDS]

# "adjudication" is a genuine procurement term (award by open tender) but in
# Walloon council French it's used just as often for a public *sale* of
# communal property/goods by auction ("mise en vente par adjudication
# publique") — nothing to do with buying goods/services. Titles containing
# this phrase are excluded even if "adjudication" matched, since it's the
# single most ambiguous keyword in the list. Found via the first dedup pass
# on the Liège/Charleroi prototype output (3/25 matches were property-sale
# false positives).
#
# "activités ambulantes sur les marchés publics" - "marché public" here
# means a physical public *marketplace* (market-day/market square), not a
# procurement contract. Found widening the pilot to 12 communes.
#
# The next three were found by cross-checking amount extraction: a batch of
# "unresolved" matches all had zero extractable EUR amount, and reading
# them showed why - they're policy motions, intercommunale general-assembly
# attendance reports, and oversight-annulment info reports, none of which
# are an actual award/tender decision, just procurement-adjacent agenda
# items that happen to use the vocabulary.
_FALSE_POSITIVE_PHRASES = [
    _fold("mise en vente"),
    _fold("activites ambulantes"),
    _fold("interpellation citoyenne"),
    _fold("motion pour une politique"),
    _fold("assemblee generale ordinaire"),
    _fold("tutelle generale d'annulation"),
]

# "Marchés publics et subsides" is a recurring council AGENDA SECTION HEADER
# in several communes (seen heavily in Ottignies-Louvain-la-Neuve) used to
# group routine subsidy/grant approvals to associations ("Subvention 2026 à
# l'ASBL ..."), not tenders. A title that only matched because of the bare
# "marché public"/"marchés publics" phrase AND is clearly a subsidy grant
# (mentions "subvention" or "cotisation") is dropped; a title carrying a
# stronger, unambiguous procurement term (cahier des charges, accord-cadre,
# appel d'offres, etc. alongside it) is kept regardless.
_WEAK_KEYWORDS = {_fold("marché public"), _fold("marchés publics")}
_SUBSIDY_MARKERS = [_fold("subvention"), _fold("cotisation")]


def match_keywords(text: str | None) -> list[str]:
    if not text:
        return []
    folded = _fold(text)
    if any(phrase in folded for phrase in _FALSE_POSITIVE_PHRASES):
        return []
    matches = [original for folded_kw, original in _FOLDED_KEYWORDS if folded_kw in folded]
    if not matches:
        return matches
    folded_matches = {_fold(m) for m in matches}
    only_weak_matches = folded_matches <= _WEAK_KEYWORDS
    if only_weak_matches and any(marker in folded for marker in _SUBSIDY_MARKERS):
        return []
    return matches
