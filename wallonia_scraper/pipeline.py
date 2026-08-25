"""Prototype pipeline: walk recent listing pages for each pilot commune,
keep decisions whose title matches a procurement anchor term, fetch the
full decision text for those matches only, and write normalized JSONL.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

from . import config
from .api_client import DeliberationsClient
from .extract import extract_amount, size_bucket
from .filter import match_keywords
from .parse import has_next_page, parse_detail_page, parse_listing_page
from .schema import ProcurementDecision

log = logging.getLogger("wallonia_scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _reference(url: str | None) -> str:
    if url:
        return url
    return "unknown-" + hashlib.sha1(url.encode() if url else b"").hexdigest()[:12]


def run_commune(client: DeliberationsClient, slug: str, display_name: str) -> list[ProcurementDecision]:
    all_cards: list[dict] = []
    b_start = 0
    for page_num in range(config.MAX_PAGES_PER_COMMUNE):
        html = client.fetch_listing_page(slug, b_start)
        cards = parse_listing_page(html)
        if not cards:
            break
        all_cards.extend(cards)
        log.info("%s: page %d -> %d cards (running total %d)", display_name, page_num + 1, len(cards), len(all_cards))
        if not has_next_page(html):
            break
        b_start += config.PAGE_SIZE

    matched = []
    for card in all_cards:
        kws = match_keywords(card["title"])
        if kws:
            matched.append((card, kws))

    log.info("%s: %d/%d recent decisions matched a procurement anchor term", display_name, len(matched), len(all_cards))

    results: list[ProcurementDecision] = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for card, kws in matched:
        description = None
        if card["source_url"]:
            try:
                detail_html = client.fetch_detail_page(card["source_url"])
                description = parse_detail_page(detail_html)
            except Exception as exc:  # noqa: BLE001 - log and keep the card without full text
                log.warning("failed to fetch detail for %s: %s", card["source_url"], exc)

        amount_result = extract_amount((description or "") + " " + (card["title"] or ""))
        amount, currency = amount_result if amount_result else (None, None)
        missing = [] if amount is not None else ["estimated_value"]

        results.append(
            ProcurementDecision(
                source="wallonia_deliberations",
                commune=display_name,
                source_reference=_reference(card["source_url"]),
                source_url=card["source_url"] or "",
                title=card["title"] or "",
                description=description,
                status=card["status"],
                matiere=card["matiere"],
                mandataire=card["mandataire"],
                numero_point=card["numero_point"],
                seance_date=card["seance_date"],
                matched_keywords=kws,
                estimated_value=amount,
                estimated_value_currency=currency,
                amount_size_bucket=size_bucket(amount),
                missing_fields=missing,
                retrieved_at=now,
            )
        )
    return results


def run(resume: bool = False) -> None:
    """resume=True skips any commune whose output file for today already
    exists, so a multi-hour full-roster run can be restarted after a crash
    without redoing already-completed communes."""
    client = DeliberationsClient()
    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")

    total = 0
    failed: list[str] = []
    for i, (slug, display_name) in enumerate(config.PILOT_COMMUNES.items(), start=1):
        out_path = out_dir / f"wallonia_deliberations_{slug}_{today}.jsonl"
        if resume and out_path.exists():
            log.info("[%d/%d] %s: already done today, skipping (resume=True)", i, len(config.PILOT_COMMUNES), display_name)
            continue

        try:
            results = run_commune(client, slug, display_name)
        except Exception:  # noqa: BLE001 - one bad commune must not kill a multi-hour full-roster run
            log.exception("[%d/%d] %s: failed, skipping this commune", i, len(config.PILOT_COMMUNES), display_name)
            failed.append(display_name)
            continue

        with out_path.open("w", encoding="utf-8") as f:
            for record in results:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        log.info("[%d/%d] %s: wrote %d matched decisions to %s", i, len(config.PILOT_COMMUNES), display_name, len(results), out_path)
        total += len(results)

    log.info("run complete: %d matched decisions across %d communes (%d failed: %s)", total, len(config.PILOT_COMMUNES), len(failed), failed)


if __name__ == "__main__":
    run()
