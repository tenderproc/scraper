"""Push enriched records from all three regional sources into the real
TenderProc product's Supabase project.

Per explicit product decision, notice_kind ('open'/'awarded'/'unclear') is
NOT a filter on what reaches the user — every scraped record goes into
external_opportunities regardless of kind, so Opportunities shows all of
them. notice_kind is still computed and stored (useful metadata for a
future badge/filter), and 'awarded' records are *additionally* dual-written
to contract_awards (the existing table, already used for TED award data)
since that's additive value for the incumbent-screening groundwork, not a
reason to exclude them from Opportunities. See
supabase-external-opportunities-migration.sql in the product repo. Only
records already confirmed as an exact duplicate of a TED notice
(dedup_status='confirmed_duplicate') are excluded on the product's read
side — that's avoiding showing the same tender twice, not threshold
filtering.

Plain REST + the service-role key (Prefer: resolution=merge-duplicates for
upsert-on-conflict), matching the product's own ingest-awards route's
upsert pattern from the Python side. No supabase-py dependency needed.

Known gap, on purpose rather than by oversight: 'awarded' records don't get
a winner_name pushed. The actual supplier name is present in free text
(e.g. "...attribué...à la SARL AMG PRO") but extracting it reliably needs
real NLP (named-entity extraction), not built here - pushing a guessed name
would violate the same never-fabricate discipline the whole scraper has
followed throughout (see extract.py's docstring). contract_awards.winner_name
is nullable specifically for cases like this.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from datetime import date, datetime

import requests
from dotenv import load_dotenv

from .extract_deadline import extract_deadline
from .extract_deadline_nl import extract_deadline_nl
from .notice_kind import classify_notice_kind

load_dotenv()

log = logging.getLogger("wallonia_scraper.push_to_supabase")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_FR_DATE_RE = re.compile(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", re.IGNORECASE)


def _parse_date(raw: str | None) -> str | None:
    """Best-effort parse of this scraper's various seance_date formats into
    an ISO date string. Returns None rather than guessing on anything that
    doesn't clearly match - a missing publication_date is honest; a wrong
    one is misleading."""
    if not raw:
        return None
    iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_match:
        return iso_match.group(1)
    fr_match = _FR_DATE_RE.search(raw)
    if fr_match:
        day, month_name, year = fr_match.groups()
        month = _FR_MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return None
    return None


def _session() -> requests.Session:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment "
            "(see README.md's 'Pushing to the product' section) before running this."
        )
    s = requests.Session()
    s.headers.update(
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
    )
    return s


def _upsert(session: requests.Session, table: str, rows: list[dict], on_conflict: str) -> None:
    if not rows:
        return
    resp = session.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params={"on_conflict": on_conflict},
        json=rows,
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"upsert into {table} failed: {resp.status_code} {resp.text[:500]}")


def _to_opportunity_row(r: dict, notice_kind: str) -> dict:
    return {
        "source": r["source"],
        "source_reference": r["source_reference"],
        "title": r["title"],
        "buyer_name": r.get("commune"),
        "description": r.get("description"),
        "cpv_codes": [],
        "estimated_value": r.get("estimated_value"),
        "estimated_value_currency": r.get("estimated_value_currency"),
        # wallonia_deliberations and flanders_gelinkt_notuleren decision
        # text often states a real submission deadline in prose even though
        # no source publishes a structured deadline field - see
        # extract_deadline.py / extract_deadline_nl.py. Returns None (not a
        # fabricated guess) for conseilcommunal (no decision text at all) or
        # for a record with no extractable absolute date - same
        # never-fabricate discipline as estimated_value.
        "deadline": (
            extract_deadline_nl(r.get("description"))
            if r["source"] == "flanders_gelinkt_notuleren"
            else extract_deadline(r.get("description"))
        ),
        # Deliberately NOT falling back to retrieved_at (the scrape timestamp)
        # when seance_date can't be parsed - that would fabricate a "just
        # published today" date (this hits ~100% of Flanders records, whose
        # seance_date is never populated) and flood the date-sorted
        # Opportunities feed above genuinely time-relevant results. A null
        # publication_date sorts last on the product side instead - same
        # never-fabricate discipline as winner_name below.
        "publication_date": _parse_date(r.get("seance_date")),
        "region": r.get("commune"),
        "source_url": r.get("source_url") or "",
        "notice_kind": notice_kind,
        "dedup_status": r.get("dedup_status", "candidate"),
    }


def _to_award_row(r: dict) -> dict:
    return {
        "source": r["source"],
        "source_reference": r["source_reference"],
        "contracting_authority": r.get("commune") or "",
        "cpv_codes": [],
        "award_date": _parse_date(r.get("seance_date")),  # see publication_date above - no retrieved_at fallback
        "winner_name": None,  # see module docstring
        "award_value": r.get("estimated_value"),
        "award_value_currency": r.get("estimated_value_currency"),
        "source_url": r.get("source_url") or "",
        "raw_title": r.get("title"),
        "updated_at": datetime.utcnow().isoformat(),
    }


def _load_jsonl_dedup_by_reference(pattern: str) -> list[dict]:
    """Like dedup_check.load_jsonl, but for source files that accumulate one
    dated file per scraper run (e.g. flanders_enriched_20260821.jsonl,
    flanders_enriched_20260822.jsonl) - a re-run rescoring the same decisions
    (dates now resolved, dedup re-checked against current BOSA) leaves the
    older file in place too, so the same (source, source_reference) can
    appear in more than one file matched by the glob. Upserting the same
    conflict key twice in a single request is rejected by Postgres ("ON
    CONFLICT DO UPDATE command cannot affect row a second time"), so this
    keeps only the last occurrence - sorting filenames first so "last" means
    "most recently dated file" (glob.glob's order isn't guaranteed)."""
    seen: dict[tuple[str, str], dict] = {}
    for fn in sorted(glob.glob(pattern)):
        with open(fn, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                seen[(record["source"], record["source_reference"])] = record
    return list(seen.values())



# deliberations.be's own site marks each agenda point's status (its
# "data-watermark" attribute, scraped verbatim into ProcurementDecision.status
# by parse.py): "Projet de décision" for a draft not yet adopted by the
# council, empty/None once adopted (parsed as None - see parse.py's _clean).
# Drafts can still change or be rejected before adoption, so they're not a
# real procurement decision - excluded from external_opportunities. Scoped
# to this one source: conseilcommunal's "status" field means something
# unrelated (open/closed session), and Flanders has no status field at all.
_DRAFT_STATUS = "Projet de décision"
_DRAFT_SOURCE = "wallonia_deliberations"
# Belt-and-suspenders text match, also used product-side (see
# lib/externalOpportunities.ts) to retroactively cover rows already pushed
# before this filter existed, where the authoritative status wasn't stored.
# Keep this to a single phrase: the scraper joins each card's DOM text nodes
# with newlines, so a longer quoted sentence has newlines where this string
# would need spaces and silently never matches (found 2026-08-23 - 300+
# legacy draft rows had leaked into external_opportunities because of this).
_DRAFT_DISCLAIMER_MARKER = "document préparatoire"


def _is_draft(r: dict) -> bool:
    if r.get("source") == _DRAFT_SOURCE and r.get("status") == _DRAFT_STATUS:
        return True
    return _DRAFT_DISCLAIMER_MARKER in (r.get("description") or "")


def push_enriched_glob(session: requests.Session, pattern: str) -> tuple[int, int, int]:
    records = _load_jsonl_dedup_by_reference(pattern)
    opp_rows, award_rows = [], []
    kind_counts = {"open": 0, "awarded": 0, "unclear": 0}
    for r in records:
        kind = classify_notice_kind(r.get("title"), r.get("description"), r.get("matiere"))
        kind_counts[kind] += 1
        if _is_draft(r):
            continue
        # Every remaining record goes to external_opportunities regardless
        # of kind - notice_kind is metadata, not a filter (explicit product
        # decision).
        opp_rows.append(_to_opportunity_row(r, kind))
        if kind == "awarded":
            # Additionally, not instead of: awarded records also feed the
            # existing contract_awards table for incumbent-screening.
            award_rows.append(_to_award_row(r))

    _upsert(session, "external_opportunities", opp_rows, "source,source_reference")
    _upsert(session, "contract_awards", award_rows, "source,source_reference")
    return kind_counts["open"], kind_counts["awarded"], kind_counts["unclear"]


def run() -> None:
    session = _session()
    sources = [
        ("data/enriched/wallonia_deliberations_enriched_*.jsonl", "Wallonia (deliberations.be)"),
        ("data/enriched/conseilcommunal_enriched_*.jsonl", "Wallonia (conseilcommunal.be)"),
        ("data/enriched/flanders_enriched_*.jsonl", "Flanders (Gelinkt Notuleren)"),
    ]
    for pattern, label in sources:
        open_n, awarded_n, unclear_n = push_enriched_glob(session, pattern)
        log.info("%s: pushed %d open + %d unclear to external_opportunities, %d awarded to contract_awards", label, open_n, unclear_n, awarded_n)


if __name__ == "__main__":
    run()
