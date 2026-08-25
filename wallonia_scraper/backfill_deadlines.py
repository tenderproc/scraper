"""One-off backfill: applies extract_deadline/extract_deadline_nl to the
existing external_opportunities rows pushed before that extraction existed
in push_to_supabase.py (see project memory - deadline was hardcoded to None
until 2026-08-23). push_to_supabase.py only runs extraction on freshly
scraped rows going forward; it never revisits rows already in Supabase, so
the historical backlog needed a separate pass. Not wired into weekly.py -
run manually, once, then the ongoing weekly push keeps new rows current on
its own.

Idempotent by design, same pattern as classify_bid_status.py: only ever
selects/updates rows where deadline IS NULL, so re-running is always safe
and only touches rows that still need it (e.g. if extraction improves
later, or a row that had no deadline gains a resolvable one after a source
correction).

Expect a small hit rate, not a large one - extract_deadline.py's own
docstring survey found only 45+/19+ records (out of thousands) contain a
real, explicit absolute submission deadline at all. Most of this backlog is
municipal council minutes that structurally never state one; this backfill
recovers what's genuinely there, not a fix for a broken extractor.
"""
from __future__ import annotations

import logging
import os

import requests
from dotenv import load_dotenv

from .extract_deadline import extract_deadline
from .extract_deadline_nl import extract_deadline_nl

load_dotenv()

log = logging.getLogger("wallonia_scraper.backfill_deadlines")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")


def _session() -> requests.Session:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment.")
    s = requests.Session()
    s.headers.update(
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
        }
    )
    return s


def _fetch_without_deadline(session: requests.Session, page_size: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = session.get(
            f"{SUPABASE_URL}/rest/v1/external_opportunities",
            params={
                "select": "source,source_reference,description",
                "deadline": "is.null",
                "limit": str(page_size),
                "offset": str(offset),
            },
            timeout=30,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"fetch failed: {resp.status_code} {resp.text[:300]}")
        page = resp.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def _update_deadline(session: requests.Session, source: str, source_reference: str, deadline: str) -> None:
    resp = session.patch(
        f"{SUPABASE_URL}/rest/v1/external_opportunities",
        params={"source": f"eq.{source}", "source_reference": f"eq.{source_reference}"},
        json={"deadline": deadline},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"update failed for {source}:{source_reference}: {resp.status_code} {resp.text[:300]}")


def run() -> None:
    session = _session()
    rows = _fetch_without_deadline(session)
    log.info("found %d rows with no deadline", len(rows))

    counts = {"recovered": 0, "still_none": 0}
    for i, row in enumerate(rows):
        description = row.get("description")
        deadline = (
            extract_deadline_nl(description)
            if row["source"] == "flanders_gelinkt_notuleren"
            else extract_deadline(description)
        )
        if deadline is not None:
            _update_deadline(session, row["source"], row["source_reference"], deadline)
            counts["recovered"] += 1
            log.info("recovered deadline %s for %s:%s", deadline, row["source"], row["source_reference"])
        else:
            counts["still_none"] += 1

        if (i + 1) % 500 == 0:
            log.info("progress: %d/%d (%s)", i + 1, len(rows), counts)

    log.info("done: %s", counts)


if __name__ == "__main__":
    run()
