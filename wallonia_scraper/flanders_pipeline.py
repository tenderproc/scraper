"""Scrape Flanders' Gelinkt Notuleren register via its search-first
strategy: query PROCUREMENT_TERMS directly against /besluiten (a real
full-text search, unlike either Wallonia source), dedupe by besluit id
across terms, then resolve each match's owning municipality/entity.

No title-keyword filter needed here (unlike both Wallonia sources) - the
search terms themselves are the procurement filter, and they're Dutch, so
filter.py's French keyword list wouldn't apply anyway. Amount extraction
reuses extract.py (language-agnostic regex on the amount itself, with
Dutch context phrases added for the "near an estimate label" heuristic).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .extract import extract_amount, size_bucket
from .flanders_client import PROCUREMENT_TERMS, GelinktNotulerenClient
from .schema import ProcurementDecision

log = logging.getLogger("wallonia_scraper.flanders")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OUTPUT_DIR = "data/ingested_flanders"

# Strip the governing-body type off a resolved org name so `commune` holds
# just the municipality/entity name (e.g. "Essen", not "College van
# Burgemeester en Schepenen Essen") - needed for BOSA authority-name
# matching in dedup_check.py, which folds and substring-matches on this
# field. Longest prefixes first so "Raad voor Maatschappelijk Welzijn"
# doesn't get shadowed by a shorter partial alternative.
_ORG_TYPE_PREFIXES = [
    "Raad voor Maatschappelijk Welzijn", "College van Burgemeester en Schepenen",
    "Bijzonder Comite voor de Sociale Dienst", "Gemeenteraad", "Vast Bureau",
    "OCMW-raad", "Politieraad", "Provincieraad", "Deputatie", "Districtsraad",
    "Districtscollege", "Burgemeester",
]


def _entity_name(org_name: str) -> str:
    for prefix in _ORG_TYPE_PREFIXES:
        if org_name.startswith(prefix):
            return org_name[len(prefix):].strip()
    return org_name


def run() -> None:
    client = GelinktNotulerenClient()
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_id: dict[str, dict] = {}
    matched_terms: dict[str, list[str]] = {}
    for term in PROCUREMENT_TERMS:
        hits = client.search_besluiten(term)
        log.info("term %r: %d hits", term, len(hits))
        for h in hits:
            by_id[h["id"]] = h
            matched_terms.setdefault(h["id"], []).append(term)

    log.info("dedup'd to %d unique besluiten across %d terms", len(by_id), len(PROCUREMENT_TERMS))

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    results: list[ProcurementDecision] = []
    failed = 0
    for i, (besluit_id, b) in enumerate(by_id.items(), start=1):
        if i % 50 == 0:
            log.info("resolving commune %d/%d", i, len(by_id))
        try:
            entity, seance_date = client.resolve_commune_and_date(besluit_id)
        except Exception:  # noqa: BLE001 - one bad besluit must not kill the whole run
            log.exception("failed to resolve commune/date for besluit %s", besluit_id)
            failed += 1
            entity, seance_date = None, None
        entity_name = _entity_name(entity) if entity else None

        title = b["attributes"].get("titel") or ""
        description = b["attributes"].get("beschrijving")
        combined_text = f"{title} {description or ''}"
        amount_result = extract_amount(combined_text)
        amount, currency = amount_result if amount_result else (None, None)
        missing = [] if amount is not None else ["estimated_value"]
        if entity is None:
            missing.append("commune")
        if seance_date is None:
            missing.append("seance_date")

        results.append(
            ProcurementDecision(
                source="flanders_gelinkt_notuleren",
                commune=entity_name or "UNKNOWN",
                source_reference=f"gelinkt-notuleren:{besluit_id}",
                source_url=b["attributes"].get("uri") or "",
                title=title,
                description=description,
                status=None,
                matiere=None,
                mandataire=entity,  # full governing-body name, e.g. "College van Burgemeester en Schepenen Essen"
                numero_point=None,
                seance_date=seance_date,
                matched_keywords=matched_terms[besluit_id],
                estimated_value=amount,
                estimated_value_currency=currency,
                amount_size_bucket=size_bucket(amount),
                missing_fields=missing,
                retrieved_at=now,
            )
        )

    today = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"flanders_gelinkt_notuleren_{today}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for record in results:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    unknown_commune = sum(1 for r in results if r.commune == "UNKNOWN")
    log.info(
        "run complete: %d matched decisions written to %s (%d commune-resolution failures, %d unknown-commune)",
        len(results), out_path, failed, unknown_commune,
    )


if __name__ == "__main__":
    run()
