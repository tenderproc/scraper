"""Configuration for the deliberations.be scraper.

Started as a 2-commune pilot (Liège, Charleroi), widened to 12 (one to a
few per Walloon province) to validate the dedup/precision numbers held up
at scale, and is now the full 206-commune deliberations.be roster (see
PILOT_COMMUNES below, loaded from communes_full_roster.json). The separate
conseilcommunal.be platform (~39 more communes, disjoint from this one) is
still untouched — future work.

At ~60-150s/commune observed during the 12-commune pilot, the full roster
run is a multi-hour job — see the project README's "Widening to 12
communes" and "Next: widening beyond 12 communes" sections for the timing
basis, and run it as an explicit background/scheduled job, not
interactively.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_URL = "https://www.deliberations.be"

USER_AGENT = "TenderProc-prototype/0.1 (research; contact procurement-bot@tenderproc.example)"

# commune slug -> display name. Slugs confirmed against
# https://www.deliberations.be/@@institution-locations (not always the
# obvious kebab-case guess - e.g. La Louvière is "lalouviere", no hyphens).
# Full roster snapshotted 2026-08-20; re-fetch communes_full_roster.json
# from that endpoint periodically since new communes can be onboarded.
_ROSTER_PATH = Path(__file__).parent / "communes_full_roster.json"
with _ROSTER_PATH.open(encoding="utf-8") as _f:
    PILOT_COMMUNES: dict[str, str] = json.load(_f)

# How many listing pages (20 decisions/page, newest session first) to walk
# per commune. This is a "recent window" analogous to BOSA_LOOKBACK_DAYS,
# not a full historical backfill.
MAX_PAGES_PER_COMMUNE = 10
PAGE_SIZE = 20

REQUEST_DELAY_SECONDS = 1.0

# Anchor terms for genuine public-procurement decisions, as French council
# decisions phrase them. Deliberately narrower than a generic "marché"/
# "opdracht" match (see the Flanders source notes on why those are noisy):
# excludes plain "marché" alone since it also means "market" in unrelated
# senses (marché de Noël, marché immobilier, etc.) in council business.
PROCUREMENT_KEYWORDS = [
    "marché public",
    "marchés publics",
    "cahier des charges",
    "cahier spécial des charges",
    "appel d'offres",
    "appel d offres",
    "adjudication",
    "accord-cadre",
    "accord cadre",
    "procédure négociée",
    "procedure negociee",
    "avis de marché",
    "passation d'un marché",
    "passation du marché",
    "passation de marché",
    "attribution du marché",
    "attribution de marché",
    "marché de faible montant",
    "marché de travaux",
    "marché de fournitures",
    "marché de services",
]

OUTPUT_DIR = "data/ingested"
