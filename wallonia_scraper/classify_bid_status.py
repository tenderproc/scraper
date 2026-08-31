"""Classifies every external_opportunities row with bid_status: the narrow
"reading only this record, could an outside company respond to a call for
bids today?" question - see supabase-external-opportunities-bid-status-
migration.sql (product repo) for why this is a separate column from
notice_kind (that one answers "will this get its own TED/BOSA notice
eventually", a different question, and is dual-written to contract_awards -
its meaning can't be repurposed).

Two tiers, cheapest first:
1. Marker short-circuit - exact port of the phrases already verified live
   in the product's lib/externalOpportunities.ts (draft disclaimer,
   negotiated-without-publication, closed shortlist by name or by the
   "consultation des X suivant(e)s" pattern, named award, empty
   description). Zero API cost, zero new false-positive risk: these are
   the same markers already proven against real data, not a guess.
2. Claude Haiku for everything the markers don't catch - the genuinely
   ambiguous bucket ("council approved specs and procedure", no further
   signal) that keyword matching structurally can't resolve. This is the
   actual judgment call a human would make reading the sentence, done at
   scale.

Idempotent by design: only ever selects/updates rows where bid_status IS
NULL, so re-running (the weekly scheduled job, right after
push_to_supabase.run()) only classifies newly-pushed rows each week -
never re-touches or re-spends on an already-classified row.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

log = logging.getLogger("wallonia_scraper.classify_bid_status")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# --- Tier 1: marker short-circuit — exact port of lib/externalOpportunities.ts.
# Keep these in sync by hand; there's no shared source of truth across the
# TS/Python boundary. If that file's markers change, update here too.

_DRAFT_DISCLAIMER_MARKER = "document préparatoire"

_NEGOTIATED_WITHOUT_PUBLICATION_MARKERS = [
    "sans publication préalable",
    "sans publicité préalable",
    "zonder voorafgaande bekendmaking",
]

_NON_TENDER_MARKERS = _NEGOTIATED_WITHOUT_PUBLICATION_MARKERS + [
    "te contacteren",
    "uit te nodigen",
    "à consulter",
    "gegund aan",
    "gunnen aan",
    "marché est attribué",
    "attribuer le marché à",
    "décide d’attribuer",  # curly apostrophe (U+2019)
    "décide d'attribuer",  # straight apostrophe (U+0027)
]

_CLOSED_SHORTLIST_CONSULTATION_RE = re.compile(
    r"consultation des.{0,60}suivante?s?\s*:", re.IGNORECASE
)

_EMPTY_DESCRIPTION_PLACEHOLDER = "geef korte beschrijving op"

# wallonia_conseilcommunal-only: a title like "Fixation des conditions et
# mode de passation d'un marché de travaux (Procédure ouverte)" names the
# award METHOD the council picked, not that bidding is open today - it's the
# same pre-tender "approving specs and procedure" administrative step this
# whole project has repeatedly found to be non-actionable (see the Kortenberg
# mandating-Herent case, the Walhain deadline-required fix). Verified live
# 2026-08-31 by comparing against the identical title pattern in
# wallonia_deliberations, which DOES have a full description: given only the
# title, the LLM tier over-trusts "procédure ouverte" as a positive signal
# and flips to open_call (7 of the first 15 recovered rows were exactly this
# pattern with no further signal); given the full description for the same
# pattern, the same model correctly returns not_biddable - once because the
# extra prose stated no concrete deadline/publication mandate, once because a
# LATER decision (invisible from the title) had cancelled the tender for lack
# of funding. Title alone can't surface either of those, so default to
# not_biddable on this pattern rather than let the LLM guess from a title
# that structurally can't support the distinction.
_AMBIGUOUS_APPROVAL_RE = re.compile(r"conditions?.{0,40}(?:mode|passation)|passation.{0,40}conditions?", re.IGNORECASE)
# ...unless a stronger, explicit call-type phrase is ALSO present - those
# name an actual invitation/announcement rather than just the chosen method,
# so it's worth letting the LLM weigh in rather than blanket-suppressing.
_STRONGER_CALL_SIGNAL_RE = re.compile(
    r"adjudication publique|appels? [aà] manifestation d[’']int[ée]r[êe]t|mise en concurrence|appel d[’']offres|avis de march[ée]",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _has_no_real_description(description: str | None) -> bool:
    stripped = re.sub(r"[\s​]+", "", description or "")
    return len(stripped) == 0 or _fold(stripped) == _EMPTY_DESCRIPTION_PLACEHOLDER.replace(" ", "")


def marker_verdict(description: str | None, source: str | None = None, title: str | None = None) -> str | None:
    """Returns 'not_biddable' if a verified marker matches, else None
    (needs the LLM tier). Never returns 'open_call' — no marker here is a
    positive signal, only a confidently-negative one; see the product
    file's own docstring for why a "positive signal" keyword approach was
    tried and abandoned."""
    desc = description or ""
    folded = _fold(desc)
    if _fold(_DRAFT_DISCLAIMER_MARKER) in folded:
        return "not_biddable"
    if _CLOSED_SHORTLIST_CONSULTATION_RE.search(desc):
        return "not_biddable"
    for marker in _NON_TENDER_MARKERS:
        if _fold(marker) in folded:
            return "not_biddable"
    # wallonia_conseilcommunal never has a description at all - conseilcommunal.be
    # structurally only publishes a title (see conseilcommunal_pipeline.py's
    # `missing = ["description", "estimated_value"]  # this platform never
    # publishes either`). An empty description there is normal, not the
    # "Geef korte beschrijving op" placeholder / genuinely-blank signal this
    # check exists for on the other two sources - short-circuiting to
    # not_biddable here would silently classify all ~1,300+ of this source's
    # rows without ever letting the LLM tier read the (often informative)
    # title. Fall through to the LLM instead, same as any other source with
    # real content.
    if source != "wallonia_conseilcommunal" and _has_no_real_description(desc):
        return "not_biddable"
    if source == "wallonia_conseilcommunal":
        title_text = title or ""
        if not _STRONGER_CALL_SIGNAL_RE.search(title_text) and _AMBIGUOUS_APPROVAL_RE.search(title_text):
            return "not_biddable"
    return None


# --- Tier 2: LLM classification for the marker-ambiguous remainder ---

_SYSTEM_PROMPT = """You classify Belgian municipal council-meeting decision records for a public-procurement monitoring product. Each record is one agenda item from a town/city council's minutes, in French or Dutch, already confirmed to NOT match any of the confidently-closed patterns (negotiated-without-publication, named shortlist, named award winner, draft/unadopted, empty).

Read the title and description and decide: reading ONLY this record, could a company outside the council (not already named/shortlisted in the text) currently submit a bid or offer in response to what's described?

Answer with exactly one JSON object, no other text:
{"classification": "open_call" | "not_biddable" | "unclear", "reason": "<one short sentence in English>"}

- "open_call": the record describes (or is actively launching) a public procurement process with real, ongoing competition open to outside bidders — e.g. it mentions an actual offer-submission deadline, explicitly names an "open procedure" (procédure ouverte / openbare procedure), or otherwise clearly invites unnamed/general bidders.
- "not_biddable": administrative housekeeping that precedes any call (approving a budget estimate, mandating another authority to run the process later, authorizing preliminary steps) with no live competition happening yet, OR a retrospective report/communication of past purchases, OR anything else that isn't a current live call — even if procurement-related.
- "unclear": you genuinely cannot tell either way after careful reading.

Default to "not_biddable" over "open_call" when genuinely torn — these records are council minutes, not tender notices, so the bar for "open_call" is a record that itself describes an active, ongoing public competition, not just "procurement is happening somewhere in this process"."""

# Appended only when the record has no description (wallonia_conseilcommunal -
# see marker_verdict's docstring). Verified live 2026-08-31: given a title
# alone, this model over-trusts "procédure ouverte" (naming the award METHOD
# a council picked) as if it meant bidding is open today, in cases where the
# same model reading the full description for an identical title pattern
# correctly said not_biddable - once for lack of any concrete deadline/
# publication statement, once because a later decision (only visible in the
# fuller record) had cancelled the tender. A title can't rule either of those
# out, so raise the bar accordingly.
_TITLE_ONLY_ADDENDUM = """

IMPORTANT: this record has no description — you are working from the title alone, with no way to check for a stated deadline, a publication mandate, or a later decision that changed the outcome. Do NOT treat "procédure ouverte" / "openbare procedure" by itself as evidence of an active call — that phrase only names which award method the council chose, and by itself is exactly as ambiguous as "approving the budget estimate". Only answer "open_call" if the title itself names an actual invitation or announcement event (e.g. "adjudication publique", "appel à manifestation d'intérêt", "mise en concurrence", "appel d'offres", "avis de marché"), not just the chosen procedure type. Otherwise default to "not_biddable"."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20))
def _classify_with_llm(title: str, description: str) -> tuple[str, str]:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set — required for the LLM classification tier.")
    # Full descriptions can run long (confirmed live up to ~49k chars);
    # truncate rather than let one outlier record blow the request up —
    # the classification-relevant content is consistently near the start.
    body_text = f"Title: {title}\n\nDescription: {(description or '')[:4000]}"
    system_prompt = _SYSTEM_PROMPT + (_TITLE_ONLY_ADDENDUM if not description else "")
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 150,
            "system": system_prompt,
            "messages": [{"role": "user", "content": body_text}],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    text = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
    # Despite the system prompt saying "no other text", Haiku consistently
    # wraps the JSON in a markdown code fence (```json ... ```) - strip one
    # off if present rather than failing to parse every single response.
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # Never guess a classification from unparseable output — fail
        # loudly so it stays NULL and gets retried next run, same
        # never-fabricate discipline as the rest of this scraper.
        raise RuntimeError(f"could not parse LLM response as JSON: {text[:300]!r}")
    classification = parsed.get("classification")
    if classification not in ("open_call", "not_biddable", "unclear"):
        raise RuntimeError(f"LLM returned an unexpected classification: {parsed!r}")
    return classification, str(parsed.get("reason", ""))[:500]


# --- Supabase I/O ---


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


def _fetch_unclassified(session: requests.Session, page_size: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = session.get(
            f"{SUPABASE_URL}/rest/v1/external_opportunities",
            params={
                "select": "source,source_reference,title,description",
                "bid_status": "is.null",
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


def _update_bid_status(
    session: requests.Session, source: str, source_reference: str, classification: str, reason: str, tier: str
) -> None:
    resp = session.patch(
        f"{SUPABASE_URL}/rest/v1/external_opportunities",
        params={"source": f"eq.{source}", "source_reference": f"eq.{source_reference}"},
        json={
            "bid_status": classification,
            "bid_status_reason": reason,
            "bid_status_source": tier,
            "bid_status_classified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"update failed for {source}:{source_reference}: {resp.status_code} {resp.text[:300]}")


def run() -> None:
    session = _session()
    rows = _fetch_unclassified(session)
    log.info("found %d unclassified rows", len(rows))

    counts = {"marker/not_biddable": 0, "llm/open_call": 0, "llm/not_biddable": 0, "llm/unclear": 0, "llm/failed": 0}
    for i, row in enumerate(rows):
        verdict = marker_verdict(row.get("description"), row.get("source"), row.get("title"))
        if verdict is not None:
            _update_bid_status(session, row["source"], row["source_reference"], verdict, "matched a known non-biddable marker", "marker")
            counts["marker/not_biddable"] += 1
            continue

        try:
            classification, reason = _classify_with_llm(row.get("title") or "", row.get("description") or "")
            _update_bid_status(session, row["source"], row["source_reference"], classification, reason, "llm")
            counts[f"llm/{classification}"] += 1
        except Exception as exc:  # noqa: BLE001 - log and continue, don't let one bad row kill the run
            log.warning("LLM classification failed for %s:%s: %s", row["source"], row["source_reference"], exc)
            counts["llm/failed"] += 1

        if (i + 1) % 50 == 0:
            log.info("progress: %d/%d (%s)", i + 1, len(rows), counts)

    log.info("done: %s", counts)


if __name__ == "__main__":
    run()
