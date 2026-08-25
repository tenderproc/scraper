"""Extract a monetary estimate from a Walloon council decision's text.

Belgian French uses "." as the thousands separator and "," as the decimal
separator (e.g. "23.250.000,00 EUR") - the opposite of US notation. We
deliberately do NOT try to classify a found amount against a specific legal
EU/national procurement threshold value here: those figures are revised
periodically (the source's own regulatory-news section noted the EU
publicity thresholds were lowered again on 1 January 2026) and hardcoding a
number we are not certain is currently correct would risk confidently
mislabeling records - the same "never fabricate" discipline the BOSA
scraper's schema already follows for its own missing_fields. Instead we
extract the raw amount and (a) note it as data, and (b) give a coarse,
clearly-labeled size bucket as a heuristic signal only, not a legal ruling.
"""
from __future__ import annotations

import re

_AMOUNT_RE = re.compile(
    # "euro" (Dutch, spelled out) has no word-boundary after "eur" - the
    # bare \b-anchored "eur" alternative below never matched it. Found
    # scraping Flanders records where "576.208,50 euro" is the norm and the
    # extractor was silently returning almost no amounts (2/1179 records).
    r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:€|euro\b|EUR\b|eur\b)(?!\s*/)",
    re.IGNORECASE,
)

_ESTIMATE_CONTEXT_RE = re.compile(
    r"montant\s+est(?:im|ime)\S*|estimation[^.]{0,40}|co[ûu]t\s+annuel\s+est(?:im|ime)\S*"
    r"|geraamde\s+waarde|raming\S*|geschat\S*\s+bedrag",  # Dutch equivalents, for Flanders records
    re.IGNORECASE,
)


def _to_float(be_number: str) -> float:
    return float(be_number.replace(".", "").replace(",", "."))


def extract_amount(text: str | None) -> tuple[float, str] | None:
    """Return (amount, "EUR") for the best-guess headline estimated value,
    or None if no monetary amount was found at all.

    Heuristic, in priority order:
    1. An amount appearing within ~60 chars after an "estimation"/"montant
       estimé" phrase (the figure the decision itself calls out as the
       estimate) - keeps lot-level unit prices from being picked over the
       actual headline estimate.
    2. Failing that, the largest amount found anywhere in the text (a
       decision's total contract value is virtually always its largest
       cited figure - lot/unit prices are smaller line items).
    """
    if not text:
        return None

    for m in _ESTIMATE_CONTEXT_RE.finditer(text):
        window = text[m.end() : m.end() + 80]
        amt_m = _AMOUNT_RE.search(window)
        if amt_m:
            return _to_float(amt_m.group(1)), "EUR"

    all_amounts = [_to_float(m.group(1)) for m in _AMOUNT_RE.finditer(text)]
    if not all_amounts:
        return None
    return max(all_amounts), "EUR"


def size_bucket(amount: float | None) -> str:
    """Coarse, non-legal size bucket - a heuristic signal only. Not a
    ruling on which Belgian/EU procurement threshold regime applies; see
    module docstring for why we don't attempt that."""
    if amount is None:
        return "unknown (no amount extracted)"
    if amount < 30_000:
        return "small (<€30k) - plausibly local/sub-threshold by size alone"
    if amount < 500_000:
        return "mid (€30k-€500k) - ambiguous by size alone"
    return "large (>=€500k) - plausibly above-threshold by size alone"
