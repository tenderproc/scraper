"""Dedup/classification check: for each Wallonia deliberations.be decision,
determine whether it's already captured by BOSA or is genuinely new
coverage.

Reworked 2026-08-20 after the full 206-commune crawl found the phrase-based
classifier (v1, still available in git history / the README's own log of
its evolution) generalized poorly (41% "unresolved" at full scale vs 20% in
the 12-commune pilot) and that its "sub-threshold marker = BOSA-exclusive"
assumption was wrong for records published via Belgium's lower NATIONAL
threshold rather than the EU one - discovered because the first 7 confirmed
real duplicates didn't all carry an EU-threshold phrase.

The rework's core change: matching against BOSA's *actual ingested data*
(via match.py's label-stripped token similarity + amount corroboration) is
now the PRIMARY signal, not a phrase heuristic. A confident match is a
fact, not a guess. Phrase-based buckets are demoted to an explicitly-
labeled low-confidence FALLBACK used only when no BOSA match is found -
and their claims are softened accordingly ("no BOSA match + sub-threshold
phrasing", not "confirmed BOSA-exclusive").
"""
from __future__ import annotations

import glob
import json
import unicodedata
from collections import Counter

from .match import match_score

MATCH_CONFIDENCE_THRESHOLD = 0.4


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def load_jsonl(pattern: str) -> list[dict]:
    records = []
    for fn in glob.glob(pattern):
        with open(fn, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def find_best_bosa_match(w: dict, bosa: list[dict]) -> tuple[dict | None, float]:
    commune_folded = _fold(w["commune"])
    candidates = [b for b in bosa if commune_folded in _fold(b.get("contracting_authority") or "")]
    best, best_score = None, 0.0
    for b in candidates:
        score = match_score(w["title"], b.get("title") or "", w.get("estimated_value"), b.get("estimated_value"))
        if score > best_score:
            best, best_score = b, score
    return best, best_score


# --- Fallback phrase heuristics, only used when no confident BOSA match ---
_EU_THRESHOLD_MARKERS = [
    "soumis a la publicite europeenne", "publicite europeenne",
    "journal officiel de l'union europeenne", "journal officiel de l union europeenne",
]
_SUB_THRESHOLD_MARKERS = [
    "sans publication prealable", "marche de faible montant", "sans mise en concurrence",
    # plural/report-style phrasing found sampling the "no marker" bucket at
    # full scale - recurring "Communication" reports recapping already-made
    # delegated small purchases, e.g. "Marches publics de faibles montants
    # relevant du budget ordinaire - Communication de marches passes".
    "marches publics de faibles montants", "marches passes par delegation",
]
_WILL_BE_PUBLISHED_MARKERS = [
    "avec publication prealable", "sera soumis a publication",
    "approbation de l'avis de marche", "approbation de l avis de marche",
]
_FRAMEWORK_CALLOFF_MARKERS = [
    "centrale d'achat", "centrale d achat", "agit en tant que centrale",
    "a adhere a un accord-cadre", "auquel le conseil communal a adhere",
]


def fallback_heuristic(w: dict) -> str:
    text = _fold((w.get("description") or "") + " " + (w.get("title") or ""))
    eu_hit = any(m in text for m in _EU_THRESHOLD_MARKERS)
    sub_hit = any(m in text for m in _SUB_THRESHOLD_MARKERS)
    will_publish_hit = any(m in text for m in _WILL_BE_PUBLISHED_MARKERS)
    callo_hit = any(m in text for m in _FRAMEWORK_CALLOFF_MARKERS)

    if callo_hit:
        return "no BOSA match + framework call-off phrasing (heuristic - plausibly new)"
    if sub_hit and not eu_hit:
        return "no BOSA match + sub-threshold phrasing (heuristic - plausibly new, NOT confirmed)"
    if eu_hit or will_publish_hit:
        return "no BOSA match yet + publication-bound phrasing (heuristic - may appear once BOSA is deeper)"
    return "no BOSA match, no phrase marker either (unclassified)"


def classify(wallonia: list[dict], bosa: list[dict]) -> list[dict]:
    results = []
    for w in wallonia:
        best, score = find_best_bosa_match(w, bosa)
        if score >= MATCH_CONFIDENCE_THRESHOLD:
            category = "CONFIRMED already in BOSA"
            detail = f"score={score:.2f} vs BOSA title: {(best.get('title') or '')[:90]}"
        else:
            category = fallback_heuristic(w)
            detail = f"best BOSA score was only {score:.2f}" if best else "no same-commune BOSA candidates at all"
        results.append({"commune": w["commune"], "title": w["title"][:100], "category": category, "detail": detail, "match_score": score})
    return results


def main() -> None:
    bosa = load_jsonl("../tenderproc_bosa_scraper/data/ingested/*.jsonl")
    wallonia = load_jsonl("data/ingested/*.jsonl")
    print(f"Loaded {len(bosa)} BOSA records, {len(wallonia)} Wallonia matched records\n")

    results = classify(wallonia, bosa)
    counts = Counter(r["category"] for r in results)
    print("=== Classification (BOSA-match-first, phrase-heuristic fallback) ===")
    for category, n in counts.most_common():
        pct = 100 * n / len(results)
        print(f"  {n:4d} ({pct:4.1f}%)  {category}")

    confirmed = [r for r in results if r["category"] == "CONFIRMED already in BOSA"]
    print(f"\n--- {len(confirmed)} confirmed BOSA matches (score >= {MATCH_CONFIDENCE_THRESHOLD}) ---")
    for r in confirmed:
        print(f"  [{r['commune']}] {r['detail']}\n      {r['title']}")


if __name__ == "__main__":
    main()
