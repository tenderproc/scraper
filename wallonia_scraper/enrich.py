"""Produce the product-ready output: every scraped decision tagged with a
dedup_status, so a downstream consumer (the real TenderProc product,
wherever its codebase lives - not present in this workspace) can decide
what to surface without re-deriving the dedup logic itself.

This is a separate pass from pipeline.py on purpose: dedup status depends
on BOSA's data *at enrichment time*, not at scrape time, and BOSA keeps
accumulating history on its own schedule. Re-running this enrichment
periodically (e.g. whenever BOSA's scheduled task has run again) lets
confirmed-duplicate coverage improve for free as BOSA's window deepens -
no Wallonia-side rescraping needed. See dedup_check.py's module docstring
and the project README's "Classifier rework" section for why dedup_status
is a confidence label, not a guarantee, for anything other than
"confirmed_duplicate".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from . import config
from .dedup_check import classify, load_jsonl

log = logging.getLogger("wallonia_scraper.enrich")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Map the fallback heuristic's verbose category strings to a short,
# stable status code a downstream consumer can switch on without parsing
# prose. "candidate" always means "no confirmed BOSA duplicate found" -
# the accompanying dedup_detail keeps the honest heuristic reasoning for
# anyone who wants it, but the status itself never overclaims confidence.
def _status_code(category: str) -> str:
    if category == "CONFIRMED already in BOSA":
        return "confirmed_duplicate"
    return "candidate"


def run() -> None:
    bosa = load_jsonl("../tenderproc_bosa_scraper/data/ingested/*.jsonl")
    wallonia = load_jsonl(f"{config.OUTPUT_DIR}/*.jsonl")
    log.info("enriching %d Wallonia records against %d current BOSA records", len(wallonia), len(bosa))

    classified = classify(wallonia, bosa)
    assert len(classified) == len(wallonia)

    out_dir = Path("data/enriched")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"wallonia_deliberations_enriched_{today}.jsonl"

    counts: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as f:
        for record, c in zip(wallonia, classified):
            record = dict(record)
            record["dedup_status"] = _status_code(c["category"])
            record["dedup_detail"] = c["category"]
            record["dedup_match_score"] = round(c["match_score"], 3)
            counts[record["dedup_status"]] = counts.get(record["dedup_status"], 0) + 1
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.info("wrote %d enriched records to %s: %s", len(wallonia), out_path, counts)


if __name__ == "__main__":
    run()
