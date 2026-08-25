"""Normalized record for a Walloon municipal-council procurement decision."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass
class ProcurementDecision:
    # Identity / dedup
    source: str  # "wallonia_deliberations"
    commune: str  # e.g. "Liège"
    source_reference: str  # unique decision URL (slug is stable per iA.Délib)
    source_url: str

    title: str
    description: str | None  # full decision text from the detail page, if fetched
    status: str | None  # "Projet de décision" or "Décision"
    matiere: str | None  # council-assigned subject category (not procurement-specific)
    mandataire: str | None  # responsible official
    numero_point: str | None
    seance_date: str | None  # raw session label, e.g. "29 juin 2026 (18:00)"

    matched_keywords: list[str] = field(default_factory=list)

    # Best-guess headline monetary estimate extracted from the decision
    # text, and a coarse, non-legal size bucket derived from it (see
    # extract.py's docstring for why this is deliberately not a threshold
    # ruling). None when no amount could be found at all.
    estimated_value: float | None = None
    estimated_value_currency: str | None = None
    amount_size_bucket: str = "unknown (no amount extracted)"

    missing_fields: list[str] = field(default_factory=list)

    retrieved_at: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
