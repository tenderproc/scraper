# TenderProc — Wallonia municipal-decisions prototype

## Pushing to the product (2026-08-21)

This scraper's output now feeds the real TenderProc product's
`/opportunities` page — see `C:\Users\youss\Downloads\tender-copilot-beta\
tender-copilot`'s `supabase-external-opportunities-migration.sql` and
`lib/externalOpportunities.ts`. `wallonia_scraper/push_to_supabase.py` reads
each source's enriched JSONL and upserts into that project's Supabase
`external_opportunities` table (plus `contract_awards` for records
classified `notice_kind='awarded'` — see `notice_kind.py`).

Set up `.env` (copy `.env.example`) with `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` — the **same values** as the product's own
`.env.local` (`NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
there, different variable names, same Supabase project). Without these set,
`weekly.py` still scrapes and writes local JSONL as normal — the push step
logs a warning and is skipped rather than failing the run.

Run it standalone: `python -m wallonia_scraper.push_to_supabase`. It's also
called automatically at the end of `weekly.py`, so once credentials are set
the existing `TenderProc-Wallonia-Scraper` scheduled task pushes fresh data
every Monday with no further setup.

**Important, by explicit product decision**: `notice_kind` is metadata, not
a filter — every scraped record reaches Opportunities regardless of
estimated value or open/awarded status. Only a record already confirmed as
an exact duplicate of a live TED notice is excluded on the product's read
side.


Proof of concept for a second TenderProc source: sub-threshold public
procurement decisions from Walloon municipal councils, published via
**deliberations.be** (built by iMio, mandated by the Walloon "publicité
active" decree of 18 May 2022 — see the memory notes from the research phase
for why this source was picked over Flanders' Gelinkt Notuleren and over
building anything for Brussels-Capital).

This is a **prototype**, not a production scraper: it targets two pilot
communes (Liège, Charleroi — see `PILOT_COMMUNES` in
`wallonia_scraper/config.py`) and only the most recent ~10 listing pages
(200 decisions) per commune, not the full historical archive.

## What it found, first run (2026-08-20)

Of the 200 most recent decisions checked per commune:
- **Liège: 17/200 (8.5%)** matched a procurement anchor term
- **Charleroi: 5/200 (2.5%)** matched

(An earlier pass matched 20 for Liège; 3 turned out to be "adjudication publique"
of communal property/land — a public *auction sale*, not a procurement
tender. "Adjudication" is ambiguous in Walloon council French; `filter.py`
now excludes any title containing "mise en vente" to drop that false-positive
class. See dedup findings below for how this was caught.)

Matches include real sub-threshold council-approved contracts with full
legal text, exact amounts, and named suppliers — e.g. a €9,438 framework-
agreement call-off for police uniforms (Liège), and a €665,900 open-procedure
services tender for construction-site safety coordination (Charleroi).

## Dedup check against BOSA (2026-08-20)

Ran `python -m wallonia_scraper.dedup_check`, two separate questions:

1. **Literal overlap with BOSA's current dataset** (title similarity, same
   commune): **0/22 matched above a 0.5 similarity threshold** (highest was
   0.41). None of these 22 decisions are already sitting in BOSA's currently-
   ingested data. Caveat: BOSA's scraper only has ~3 days of history so far,
   so a "no" here mostly reflects that narrow window, not that the source is
   necessarily BOSA-exclusive in principle.

2. **Structural threshold classification** (does the decision's own text say
   it's "soumis à la publicité européenne", i.e. above the EU threshold and
   therefore should already/eventually carry its own BOSA/TED notice, versus
   an explicit sub-threshold marker like "sans publication préalable" /
   "marché de faible montant"):
   - **8/22 (36%) explicitly sub-threshold** — genuinely BOSA-exclusive,
     will never appear there regardless of window.
   - **4/22 (18%) explicitly above-EU-threshold** — these *do* have (or will
     have) a BOSA/TED notice; their absence from BOSA right now is a window
     artifact, not new coverage. A production pipeline should suppress or
     merge these rather than count them as novel.
   - **10/22 (46%) no explicit marker** — ambiguous with simple text
     matching; needs either a better classifier or manual review before
     trusting the "novel" count.

**Bottom line so far:** conservatively, at least 8/22 (36%) of this
prototype's output is genuinely new coverage BOSA structurally cannot
provide; the true number is likely higher once the "no explicit marker"
bucket is resolved, but shouldn't be assumed to be the full 22.

## Digging into the "no marker" bucket (2026-08-20)

Manually reading the 10 originally-ambiguous records surfaced two more
reliable signals the simple EU/sub-threshold marker lists missed entirely,
now added to `dedup_check.py`:

- **"will be/already officially published"** — phrases like *"avec
  publication préalable"* or *"sera soumis à publication"* are a reliable
  Belgian-procurement-law signal that a formal notice is going through
  official publication, independent of the word "européenne". These should
  eventually surface on BOSA under this commune's name — not new coverage.
- **"framework call-off"** — decisions that draw down from a framework
  tendered by a *third party* (a federal/regional "centrale d'achat" like
  Police Fédérale, SPW, or VITO, or another commune's own framework) rather
  than a fresh tender by this commune. These are genuinely new award/spend
  intelligence (who's buying what, from whom, at what price) that BOSA would
  never show under this commune's name — new coverage, even though the
  underlying framework isn't itself novel.

## Widened to 12 communes (2026-08-20)

Widened `PILOT_COMMUNES` from the original 2 to 12 — one to a few sizeable
communes per Walloon province (Liège, Charleroi, Namur, Mons, Tournai,
Verviers, La Louvière, Mouscron, Ottignies-Louvain-la-Neuve, Arlon, Seraing,
Herstal) — to check whether the match rate and dedup split generalize.

**Two real filter bugs surfaced at this wider scale**, both now fixed in
`filter.py`:
- **"marché public" also means a physical public *marketplace*** (market
  day/market square) in French, not just "procurement contract" — e.g.
  *"activités ambulantes sur les marchés publics"* (street-vendor
  regulation) is unrelated to tendering.
- **"Marchés publics et subsides" is a recurring council agenda SECTION
  HEADER**, used in several communes (heavily in
  Ottignies-Louvain-la-Neuve) to group routine subsidy/grant approvals to
  associations — not tenders. This single pattern alone explained most of
  OLLN's inflated match rate (35/200 before the fix, 9/200 after). Fixed by
  dropping any match that (a) only hit the bare "marché public"/"marchés
  publics" phrase, with no stronger procurement term alongside it, AND
  (b) mentions "subvention"/"cotisation".

**Results after both fixes, across all 12 communes** (2,200 recent decisions
scanned, 200/commune except Mouscron):

| Commune | Matches/200 |
|---|---|
| Arlon | 15 |
| Liège | 17 |
| Tournai | 7 |
| Herstal | 7 |
| La Louvière | 7 |
| Charleroi | 5 |
| Seraing | 5 |
| Verviers | 4 |
| Ottignies-Louvain-la-Neuve | 9 |
| Namur | 0 |
| Mons | 0 |
| Mouscron | 0/0 — registered but has no "Décisions" content published, only "Publications"; a real minor adoption-depth gap, not a bug |

**76 total matched decisions.** Namur and Mons genuinely scored zero (parser
verified working correctly on both — their most recent 200 decisions just
didn't include procurement items in that window) — match rate is uneven
across communes (0–8.5%), not a fixed percentage.

**Final structural classification across all 76** (still 0/76 literal
overlap with BOSA's current data):
- **24 (32%) sub-threshold** — new coverage
- **6 (8%) framework call-off** — new coverage
- **27 (36%) will be/already officially published** — not new
- **19 (25%) still no marker even after the manual-review-derived rules** —
  genuinely needs a real classifier or manual review, not more keyword lists

**Revised bottom line:** at least **30/76 (39%) is confirmed new coverage**,
at least 36% is confirmed not-new, and roughly a quarter remains genuinely
unresolved by text-pattern matching alone.

## Amount extraction + one more false-positive pass (2026-08-20)

Added `extract.py`: pulls a headline EUR amount out of each decision's text
(Belgian number format, "." thousands / "," decimal) into new
`estimated_value`/`estimated_value_currency`/`amount_size_bucket` fields on
`ProcurementDecision`, matching BOSA's own schema shape for eventual
merging. Deliberately does **not** classify against a specific legal EU/
national threshold figure — those are revised periodically (the source's
own regulatory-news section noted the EU publicity thresholds were lowered
again on 1 January 2026) and hardcoding a number we're not certain is
currently correct would risk confidently mislabeling records. `size_bucket`
is a clearly-labeled heuristic only.

This paid off as a cross-check, not just an added field: **all 19 records
in the "unresolved" bucket had zero extractable EUR amount**, and reading
them showed why — several weren't actual procurement decisions at all, just
procurement-*adjacent* agenda items: policy motions ("Motion pour une
politique communale d'achats et de marchés publics responsables"),
intercommunale general-assembly attendance reports, and an oversight-
annulment informational report to the council. Added three more exclusion
phrases to `filter.py` for these. Match count dropped 76 → **71**, and the
unresolved bucket dropped 19 → **14 (20%)**.

**Final classification, 71 records, all fixes applied:**
- **24 (34%) sub-threshold** — new coverage
- **6 (8%) framework call-off** — new coverage
- **27 (38%) will be/already officially published** — not new
- **14 (20%) still unresolved** — genuinely needs a real classifier or
  manual review; these are decisions with real procurement vocabulary but
  no stated amount and no phrase marker either way, not further reducible
  by pattern-matching alone.

**→ 30/71 (42%) confirmed new coverage**, up from the initial 36% floor on
the 2-commune sample.

## BOSA-side dedup validation attempt (2026-08-20)

Tried validating "will these above-threshold candidates actually surface on
BOSA" directly against BOSA's own live search API (`/api/sea/search/
publications`, free-text `terms` param) rather than deepening our local
BOSA scraper's ingestion window (which would mean re-scraping the entire
national feed for months back just to check a handful of records - too
heavy for a validation check).

**Finding: BOSA's free-text search relevance ranking is too weak to
reliably confirm or deny a specific known tender is in its index.** Several
targeted queries for Charleroi's "Marché N° 2024-33" ERP system tender
(whose own decision text says its "avis de marché initial" was sent to the
Bulletin des Adjudications on 26 Nov 2025) never surfaced it in the top
results, despite `totalCount`s in the tens of thousands suggesting the
query terms were far too loosely matched. A real dedup validation against
BOSA needs structured matching (reference numbers, CPV codes, authority
names) against actually-ingested BOSA records — search-relevance guessing
isn't a reliable substitute.

## Full 206-commune crawl (2026-08-20)

Ran the full deliberations.be roster (`communes_full_roster.json`, snapshotted from
`@@institution-locations`) — **1,393 matched decisions, 0 failures, ~1h38m total**,
far faster than the ~4-6h worst-case estimate (many communes have few or zero
recent procurement decisions, keeping per-commune time low). 176/206 communes
had at least one match; 30 had zero. Top by volume: Wavre (33), Clavier (32),
Momignies (31), Farciennes (29), Limbourg (28) — not dominated by the biggest
cities, since match count depends on session content, not city size alone.

Added resilience before launching (`pipeline.py`): one failed commune now logs
and skips rather than killing a multi-hour run, and `python -m
wallonia_scraper.run --resume` skips communes whose output file already
exists for today, so an interrupted run can restart without redoing
completed work.

**Important finding — the dedup check found real overlaps for the first
time.** Wallonia's BOSA scraper's own scheduled task fired automatically
during this run (registered earlier for 07:00/15:00 daily - see
[[project_tenderproc_bosa_scraper]]), refreshing BOSA's data to 1,017
records. `dedup_check.py`'s literal-overlap check found **7 confirmed
genuine duplicates** — e.g. Tinlot's road-resurfacing tender and Herstal's
UREBA energy-renovation tender appear in both sources with matching titles
and BOSA publication dates within days of the Wallonia decision. This is
the first hard proof the dedup mechanism works when the two sources'
windows actually overlap.

**This also revealed a real gap in the classification logic**: several of
those 7 confirmed-duplicate records don't contain the "publicité
européenne" EU-threshold phrase my classifier checks for — they're on BOSA
via **Belgium's lower national publication threshold**, not the EU one.
The phrase-based "sub-threshold marker = BOSA-exclusive" bucket therefore
likely **overclaims novelty** for some records; literal overlap against
real BOSA data is more authoritative where available, but is itself
limited by BOSA's narrow ~5-6 day rolling window today.

**Full-scale structural classification (1,393 records):**
- 533 (38%) sub-threshold marker
- 95 (7%) framework call-off
- 198 (14%) will be/already officially published
- **567 (41%) unresolved** — roughly double the 12-commune pilot's 20% rate

**Headline honest finding:** the classifier generalizes noticeably worse to
the wider, often smaller/rural commune set than the 12-commune pilot
suggested — likely because smaller communes' decision text is terser and
skips the verbose legal-citation phrasing (e.g. "avec publication
préalable") the phrase markers depend on. Combined with the national-vs-EU-
threshold gap just found, the honest "confirmed new coverage" number at
full scale needs the classifier reworked (real NLP/amount-threshold logic,
and awareness of the national threshold, not just the EU one) before being
trusted at this scale - the pilot's 42% "confirmed new" figure should not be
assumed to hold at the 206-commune level without that work.

## Classifier rework (2026-08-20)

Reworked `dedup_check.py` so matching against BOSA's *actual ingested data*
is the primary signal, with phrase heuristics demoted to an explicitly
low-confidence fallback used only when no BOSA match is found - the full
206-commune run showed the phrase-first design generalized poorly and
mis-labeled some nationally-thresholded (not just EU-thresholded) tenders
as "BOSA-exclusive".

New `match.py`: the old raw-character-sequence title similarity was both
too weak (missed real matches like Wavre, buried under a department-label
prefix) and too easily fooled by boilerplate phrasing (a false "match"
between two completely different Rebecq purchases - a truck crane and road
salt - that only shared the sentence template "Marché de fournitures -
acquisition de..."). Replaced with: strip a department-label prefix
(cut at the last dash/colon separator within the first 80 characters -
simpler and far more robust than trying to pattern-match label grammar,
since real French labels routinely mix capitalized and lowercase words in
ways a capitalization-based regex can't reliably distinguish from a
sentence), then compare on significant word tokens (Jaccard set
similarity) rather than character sequences, with a bonus for a closely
matching extracted amount.

**Result: 5/1,393 (0.4%) confirmed duplicates** at threshold 0.4 (up from
4, and correctly dropping the earlier false-positive Rebecq match found
before this rework - see the "Full 206-commune crawl" section above,
which reported that one as confirmed; it wasn't). Wavre alone now
correctly matches two separate BOSA tenders (road-trottoir works and a
Basilica restoration) that the old method missed or conflated.

**The honest conclusion from this rework isn't a new "% new coverage"
figure - it's that no such figure can currently be trusted.** Only 5 records
are confirmed against real BOSA data; the other 1,388 are "no match found,"
which mostly reflects BOSA's own ~1-week ingestion window, not evidence
those records are genuinely novel. The phrase-based fallback categories are
now labeled accordingly (e.g. "heuristic - plausibly new, NOT confirmed")
rather than asserted as fact. A trustworthy coverage number requires BOSA
itself to be backfilled much further back - a separate, large undertaking
(see the "BOSA-side dedup validation attempt" section above on why a
targeted search-based shortcut didn't work either).

## Making this integration-ready (2026-08-20)

The actual TenderProc product codebase (Paddle billing, Supabase Auth,
etc.) is not in this workspace, so this prototype can't be wired into a
live product from here. Instead, built the interface a real integration
would consume:

- **`enrich.py`** - a separate pass (deliberately not folded into
  `pipeline.py`) that tags every scraped decision with `dedup_status`
  (`"confirmed_duplicate"` or `"candidate"`), `dedup_detail` (the honest
  heuristic reasoning, never overclaimed), and `dedup_match_score`. Kept
  separate from scraping because dedup status depends on BOSA's data *at
  enrichment time*, not scrape time - re-running just this pass lets
  confirmed-duplicate coverage improve for free as BOSA's own window
  deepens, with zero Wallonia-side rescraping. Output:
  `data/enriched/wallonia_deliberations_enriched_<date>.jsonl`.
- **`weekly.py` + `scripts/register_windows_task.ps1`** - a scheduled job
  (registered as `TenderProc-Wallonia-Scraper`, Mondays 06:00) chaining a
  full-roster re-scrape (`resume=True`) and re-enrichment, mirroring the
  BOSA scraper's own Windows Scheduled Task. Weekly, not twice-daily like
  BOSA, since council sessions are monthly - this operationalizes "dedup
  confidence improves automatically over time" rather than leaving it as
  a plan that needs someone to remember to re-run it.
- One more real filter fix from sampling the "no marker" bucket: added
  plural/report-style sub-threshold phrasing ("marchés publics de faibles
  montants", recurring council "Communication" reports recapping
  delegated small purchases) to `_SUB_THRESHOLD_MARKERS` - moved 565→562
  unclassified. Small effect; confirms further keyword-list tweaking has
  hit diminishing returns, same conclusion the classifier-rework section
  above already reached: the remaining ~40% needs real NLP, not more
  phrase lists.

Current state on disk: **1,393 candidate records, 5 confirmed duplicates**
(auto-suppressible by a consumer checking `dedup_status`), refreshed
weekly.

## Second source: conseilcommunal.be (2026-08-20)

Added the second, disjoint Walloon platform mentioned since the original
research phase (39 communes, confirmed no overlap with deliberations.be's
206). Structurally different from deliberations.be in three ways:

1. **Real JSON REST API**, not server-rendered HTML - `conseilcommunal_client.py`
   hits `GET /ApiCitoyen/public/v1/commune/{id}/seance/{sessionId}`
   directly, no HTML parsing needed.
2. **No full legal decision text.** `Motivations`/`Decisions` fields are
   consistently `null`, even on closed/decided sessions - this platform
   publishes agenda point *titles* and structured metadata only, never the
   full minutes deliberations.be provides. `description` and
   `estimated_value` are always in `missing_fields` for this source - a
   real, structural limitation, not a scraping gap to fix.
3. **A genuine procurement category exists in the data**: the `Matiere`
   field takes real values including `"Marchés publics"` / `"MARCHES
   PUBLICS"` - a much more precise primary filter than deliberations.be's
   keyword-matching-on-title approach (which was necessary there because no
   such category exists). Used as the primary filter in
   `conseilcommunal_pipeline.py`, with the existing keyword list as a
   fallback for communes whose Matiere labeling doesn't use that value.

Took all sessions per commune (no "recent window" cap, unlike
deliberations.be) since per-commune volume here is modest (0-45 sessions
lifetime) - no per-decision detail fetch needed either, since points come
embedded in the session response, making this considerably faster to
crawl than deliberations.be per record.

**Result: 1,367 matched decisions across 39 communes, 0 failures, ~32
minutes.** Real examples: "Marché public de fourniture d'électricité",
"Acquisition d'une nouvelle camionnette plateau", "Concession de services :
Exploitation d'une crèche communale". 5 communes (Waterloo, Herve,
Plombières, Rumes, Musson) had zero sessions published at all - registered
but inactive, same adoption-gap pattern as Flanders' Gelinkt Notuleren and
deliberations.be's own zero-match communes.

**Dedup against BOSA: only 1/1,367 (0.07%) confirmed, and 96.7%
"unclassified"** by the phrase-heuristic fallback - expected and honest,
not a bug: that fallback is calibrated against deliberations.be's verbose
legal citations ("avec publication préalable", etc.), which this source
simply doesn't have. A real precision read on this source's novelty would
need to lean on the `Matiere` category match itself (already high-
confidence) rather than the phrase heuristic, which structurally doesn't
apply here.

Wired into the same automation as the first source: `weekly.py` now
chains all four steps (deliberations.be scrape → enrich → conseilcommunal
scrape → enrich) under the existing `TenderProc-Wallonia-Scraper`
scheduled task - no new task registered, just extended the one that
already exists.

**Combined state across both sources, as of this run:** 2,760 candidate
records (1,393 + 1,367), 6 confirmed duplicates, refreshed weekly.

## Third source: Flanders' Gelinkt Notuleren (2026-08-21)

Checked the Flanders option from the original research phase, now that two
working Wallonia scrapers exist to validate the pattern against. Structurally
the most different of the three sources:

- **Search-first, not enumerate-first.** `/besluiten?filter[titel]=<term>`
  is a genuine full-text search across every decision in the system, so
  unlike both Wallonia sources this doesn't need to enumerate every
  municipality/session - just query 5 Dutch procurement anchor terms
  (`aanbesteding`, `bestek`, `lastvoorwaarden`, `mededingingsprocedure`,
  `overheidsopdracht` - deliberately excluding bare `gunning`/`opdracht`,
  proven too ambiguous in the original research) directly and resolve each
  hit's owning municipality via a relationship chain (besluit ->
  besluitenlijst -> zitting -> bestuursorgaan -> is-tijdsspecialisatie-van),
  cached aggressively since many decisions share a session/org.
- **Result: 1,179 unique matched decisions, 0 resolution failures, 15
  unknown-commune, ~1h resolving** (dominated by the relationship-chain
  hops, ~1290 raw hits deduped to 1179 unique besluiten first).
- **Confirms the original research's adoption finding, precisely.** Despite
  319 registered municipalities, matches concentrate in the same small set
  found during the earlier census: Lanaken (436), Kortenberg (160), Essen
  (158), Linkebeek (150), Vlaams-Brabant province (134), Limburg province
  (55), Kapellen (38), Baarle-Hertog (12) - only 13 distinct entities total.
  This is the strongest confirmation yet that Flanders' voluntary adoption
  is real but genuinely narrow, unlike Wallonia's legally-mandated breadth.

**Two real bugs found and fixed building this:**
1. **Amount extraction missed nearly everything at first** (2/1179) -
   Dutch spells out "euro" rather than using "EUR"/"€", and the regex's
   `\b` word-boundary after "eur" never matches inside "euro" (no boundary
   between "r" and "o"). Fixed in `extract.py`; re-running extraction
   against the already-scraped data (no re-scrape needed) recovered 66
   more amounts (68/1179, 5.8% - still low since many decisions summarize
   without a figure, but no longer silently broken).
2. **`commune` field held the full governing-body name** ("College van
   Burgemeester en Schepenen Essen") instead of just the municipality
   ("Essen") - meant BOSA authority-name matching could never work, since
   BOSA never uses that phrasing. Fixed by stripping known org-type
   prefixes (same list used during the original census); the full org name
   is kept in `mandataire` instead. Patched the already-scraped file in
   place rather than re-scraping.

**Dedup against BOSA: 0/1,179 (0%) confirmed** - checked this is genuinely
the same narrow-BOSA-window issue as the other two sources, not a new bug:
BOSA's current data has zero-to-single-digit candidates for these specific
small towns (0 for Lanaken/Kortenberg/Kapellen, 1 for "Essen" - itself a
false positive, matching inside "Tessenderlo" as a substring, a real if
minor limitation of short-name commune matching worth knowing about but not
worth fixing given it's caught downstream by token similarity anyway).

Wired into the same `weekly.py` automation as both Wallonia sources - one
scheduled task now covers all three.

**Combined state across all three sources: 3,939 candidate records (1,393 +
1,367 + 1,179), 6 confirmed duplicates, refreshed weekly.**

**Verdict on the original "lower priority" call:** confirmed correct.
Flanders adds real volume (1,179 decisions) but from a narrow, already-
known set of ~13 active entities rather than broad coverage - worth having
now that it was nearly free to add (reusing the whole pipeline), but it
doesn't change the earlier prioritization: Wallonia's two sources remain
the stronger bet for anything requiring broad geographic coverage.

## Next: widening beyond 12 communes

The full deliberations.be roster is 206 communes; conseilcommunal.be (a
separate, disjoint platform) covers ~39 more. At the per-commune runtime
observed in this prototype (roughly 60-150s each, depending on match count
and detail-page fetches, at the current politeness delay), the full 206
would take on the order of **4-6 hours of continuous scraping** — not
something to launch silently. Widening `PILOT_COMMUNES` further is
mechanical (add slugs, confirmed via `/@@institution-locations`), but a
real next step should either (a) run as an explicit, scheduled long job
(mirroring how the BOSA scraper itself runs on a Windows Scheduled Task
rather than interactively), or (b) prioritize by commune size/population
rather than crawling all 206 in registration order, since match rate and
value density likely correlate with commune size more than anything else.

## Running it

```bash
pip install -r requirements.txt
python -m wallonia_scraper.run
```

Output: `data/ingested/wallonia_deliberations_<commune>_<date>.jsonl`, one
normalized `ProcurementDecision` record per matched agenda item (see
`wallonia_scraper/schema.py`).

## How it works

1. `api_client.py` — plain HTTP GET against deliberations.be's
   `@@faceted_query` listing endpoint (server-rendered HTML, no JSON API for
   decision content — unlike Flanders' Gelinkt Notuleren) and individual
   decision detail pages.
2. `parse.py` — BeautifulSoup parsing of the listing cards (title, séance
   date, matière, mandataire, status, permalink) and of the detail page's
   full legal decision text.
3. `filter.py` — accent-insensitive keyword matching against
   `PROCUREMENT_KEYWORDS` in `config.py`. Deliberately anchored on specific
   phrases (*marché public*, *cahier des charges*, *accord-cadre*,
   *adjudication*, ...) rather than the bare word *marché*, which also means
   "market" in unrelated contexts (marché de Noël, etc.).
4. `pipeline.py` — orchestrates per-commune: page through the listing,
   filter by title, fetch full text only for matches (keeps the impolite
   part of the crawl — full-page fetches — limited to the ~2-10% that
   actually matter).

## Known gaps / next steps before this is production-worthy

- **No dedup against BOSA.** These are genuinely sub-threshold in most
  cases, but a real pipeline needs to check `source_reference`/title/amount
  against the existing BOSA `ingested_tenders` table before treating
  everything here as "new."
- **Title-only keyword matching will both miss and over-match.** A
  decision whose title doesn't mention a procurement term but whose body
  does (or vice versa) won't be classified correctly by this simple pass.
  Real precision/recall numbers need manual review of a labeled sample.
- **`Matière` is a general subject taxonomy, not a procurement category**
  (see the research notes) — not used for filtering here, only kept as
  metadata.
- **Only 2 of the platform's 206 covered communes.** Scaling to more communes
  is mechanical (add to `PILOT_COMMUNES`) but means proportionally more
  requests — worth adding real rate-limit/backoff tuning before doing that.
- **`status` is `null` for already-adopted ("Décision") items** — the site
  only marks *draft* items with a watermark/status badge on the listing
  card; final decisions don't carry the same attribute. Not a bug, just
  means "no status" ≠ "no data" here.
