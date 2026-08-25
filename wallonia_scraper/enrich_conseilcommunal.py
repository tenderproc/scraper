"""Same purpose as enrich.py, for the conseilcommunal.be source. Kept as a
separate module (not a parameter on enrich.py) because the two sources'
raw output lives in different directories and the fallback phrase-
heuristic in dedup_check.classify() is calibrated against
deliberations.be's verbose legal text - it under-fires here (96.7%
"unclassified" on first run) since conseilcommunal.be publishes only
agenda titles, not full decision text. That's an honest structural
limitation of this source, not a bug to chase away with more keywords.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .conseilcommunal_pipeline import OUTPUT_DIR
from .dedup_check import classify, load_jsonl

log = logging.getLogger("wallonia_scraper.enrich_conseilcommunal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _status_code(category: str) -> str:
    return "confirmed_duplicate" if category == "CONFIRMED already in BOSA" else "candidate"


def run() -> None:
    bosa = load_jsonl("../tenderproc_bosa_scraper/data/ingested/*.jsonl")
    records = load_jsonl(f"{OUTPUT_DIR}/*.jsonl")
    log.info("enriching %d conseilcommunal.be records against %d current BOSA records", len(records), len(bosa))

    classified = classify(records, bosa)
    out_dir = Path("data/enriched")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"conseilcommunal_enriched_{today}.jsonl"

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
