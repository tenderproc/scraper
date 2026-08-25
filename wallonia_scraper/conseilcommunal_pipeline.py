"""Scrape all 39 conseilcommunal.be communes: every session (volumes are
modest here, ~20-45 sessions/commune lifetime, so we take all of them
rather than a recent-window slice like the deliberations.be pipeline),
keep points whose Matiere category is procurement-related OR whose title
matches the same keyword list used for deliberations.be (kept as a
fallback in case Matiere labeling isn't consistent across communes - it's
free text set by each commune's own admin, not a fixed enum).

No per-decision detail fetch needed here (Points come embedded in the
session response), and there's no full legal text to extract an amount
from (Motivations/Decisions fields are consistently null even on closed
sessions - this platform only publishes agenda titles + category metadata,
not full minutes like deliberations.be). Both gaps are recorded in
missing_fields rather than silently left unexplained.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import unicodedata
from pathlib import Path

from . import config
from .conseilcommunal_client import ConseilCommunalClient
from .filter import match_keywords
from .schema import ProcurementDecision

log = logging.getLogger("wallonia_scraper.conseilcommunal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OUTPUT_DIR = "data/ingested_conseilcommunal"


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _is_procurement_matiere(matiere: str | None) -> bool:
    if not matiere:
        return False
    folded = _fold(matiere)
    return "marche" in folded and "public" in folded


def run_commune(client: ConseilCommunalClient, commune_id: int, display_name: str) -> list[ProcurementDecision]:
    sessions = client.list_sessions(commune_id)
    log.info("%s: %d total sessions", display_name, len(sessions))

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    results: list[ProcurementDecision] = []
    for s in sessions:
        try:
            detail = client.get_session_detail(commune_id, s["Id"])
        except Exception as exc:  # noqa: BLE001 - one bad session must not kill the commune's run
            log.warning("%s: failed to fetch session %s: %s", display_name, s["Id"], exc)
            continue

        for p in detail.get("Points") or []:
            if p.get("isFile"):
                continue  # attachment placeholder (e.g. the convening notice PDF), not a decision
            title = p.get("Titre") or ""
            matiere = p.get("Matiere")
            kws = match_keywords(title)
            if not (_is_procurement_matiere(matiere) or kws):
                continue

            missing = ["description", "estimated_value"]  # this platform never publishes either
            results.append(
                ProcurementDecision(
                    source="wallonia_conseilcommunal",
                    commune=display_name,
                    source_reference=f"conseilcommunal:{commune_id}:{p['Id']}",
                    source_url=f"https://www.conseilcommunal.be/commune/{commune_id}/seance/{s['Id']}",
                    title=title,
                    description=None,
                    status="Closed" if detail.get("Closed") else "Open/upcoming",
                    matiere=matiere,
                    mandataire=p.get("Service"),
                    numero_point=str(p.get("Ordre")) if p.get("Ordre") is not None else None,
                    seance_date=s.get("StartDate"),
                    matched_keywords=kws,
                    missing_fields=missing,
                    retrieved_at=now,
                )
            )
    return results


def run() -> None:
    client = ConseilCommunalClient()
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")

    communes = client.list_communes()
    log.info("conseilcommunal.be roster: %d communes", len(communes))

    total = 0
    failed: list[str] = []
    for i, c in enumerate(communes, start=1):
        display_name, commune_id = c["Name"], c["Id"]
        out_path = out_dir / f"conseilcommunal_{commune_id}_{today}.jsonl"
        try:
            results = run_commune(client, commune_id, display_name)
        except Exception:  # noqa: BLE001
            log.exception("[%d/%d] %s: failed, skipping this commune", i, len(communes), display_name)
            failed.append(display_name)
            continue

        with out_path.open("w", encoding="utf-8") as f:
            for record in results:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        log.info("[%d/%d] %s: wrote %d matched decisions to %s", i, len(communes), display_name, len(results), out_path)
        total += len(results)

    log.info("run complete: %d matched decisions across %d communes (%d failed: %s)", total, len(communes), len(failed), failed)


if __name__ == "__main__":
    run()
