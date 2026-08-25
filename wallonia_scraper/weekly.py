"""Single entrypoint for the scheduled job: re-scrape all 206 communes,
then enrich against whatever BOSA data exists at that moment.

Council sessions are monthly, not daily, so unlike BOSA's twice-daily
cadence a weekly run is more than enough freshness - see
register_windows_task.ps1. resume=True on the scrape means a mid-run
crash (or a same-day manual re-run) doesn't repeat already-completed
communes for that day.
"""
from __future__ import annotations

import logging

from . import (
    classify_bid_status,
    conseilcommunal_pipeline,
    enrich,
    enrich_conseilcommunal,
    enrich_flanders,
    flanders_pipeline,
    pipeline,
    push_to_supabase,
)

log = logging.getLogger("wallonia_scraper.weekly")

if __name__ == "__main__":
    pipeline.run(resume=True)
    enrich.run()
    conseilcommunal_pipeline.run()
    enrich_conseilcommunal.run()
    flanders_pipeline.run()
    enrich_flanders.run()
    try:
        push_to_supabase.run()
    except RuntimeError as exc:
        # Missing Supabase credentials shouldn't fail the whole weekly run -
        # the local enriched JSONL is still written either way.
        log.warning("skipping Supabase push: %s", exc)
    try:
        # Runs after the push so it only ever sees rows with bid_status
        # still NULL - freshly-pushed rows this run, plus any from a
        # previous run that failed classification and got left NULL for
        # retry (see classify_bid_status.py's docstring for why that's the
        # idempotency design, not just a happy accident).
        classify_bid_status.run()
    except RuntimeError as exc:
        log.warning("skipping bid_status classification: %s", exc)
