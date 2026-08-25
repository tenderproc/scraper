"""Same purpose as enrich.py, for the Flanders Gelinkt Notuleren source."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .dedup_check import classify, load_jsonl
from .flanders_pipeline import OUTPUT_DIR

log = logging.getLogger("wallonia_scraper.enrich_flanders")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _status_code(category: str) -> str:
    return "confirmed_duplicate" if category == "CONFIRMED already in BOSA" else "candidate"


def run() -> None:
    bosa = load_jsonl("../tenderproc_bosa_scraper/data/ingested/*.jsonl")
    records = load_jsonl(f"{OUTPUT_DIR}/*.jsonl")
    log.info("enriching %d Flanders records against %d current BOSA records", len(records), len(bosa))

    classified = classify(records, bosa)
    out_dir = Path("data/enriched")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"flanders_enriched_{today}.jsonl"

    counts: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as f:
        for record, c in zip(records, classified):
            record = dict(record)
            record["dedup_status"] = _status_code(c["category"])
            record["dedup_detail"] = c["category"]
            record["dedup_match_score"] = round(c["match_score"], 3)
            counts[record["dedup_status"]] = counts.get(record["dedup_status"], 0) + 1
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.info("wrote %d enriched records to %s: %s", len(records), out_path, counts)


if __name__ == "__main__":
    run()
