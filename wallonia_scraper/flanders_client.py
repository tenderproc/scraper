"""Client for Flanders' "Gelinkt Notuleren" register
(publicatie.gelinkt-notuleren.vlaanderen.be) - see the project's memory
notes (project_tenderproc_gelinkt_notuleren_source.md) for the research
that found this source: a real JSON:API, legally established July 2023,
but with partial/uneven adoption across Flanders' 319 municipalities
(unlike Wallonia's legally-mandated ~79% coverage).

Unlike both Wallonia sources, this one has a genuinely useful top-level
full-text search on decisions themselves (`/besluiten?filter[titel]=...`),
so the scrape strategy here is search-first rather than
enumerate-every-commune-and-session: query a handful of Dutch procurement
anchor terms directly against the whole system, then resolve each match's
owning municipality via a relationship chain (besluit -> besluitenlijst ->
zitting -> bestuursorgaan -> is-tijdsspecialisatie-van -> named org),
caching aggressively since many besluiten share the same session/org.

Known API bug (found during the earlier census, still true here): combining
`sort=` with `include=` on `/zittingen` silently returns stale data. Not
triggered by this module since it never sorts+includes zittingen - flagging
here so it isn't reintroduced if this client is extended.
"""
from __future__ import annotations

import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config

BASE_URL = "https://publicatie.gelinkt-notuleren.vlaanderen.be"
HEADERS = {"Accept": "application/vnd.api+json"}

# Deliberately excludes bare "gunning"/"opdracht" - both proven too noisy
# in the earlier research (gunning also means building-permit-adjacent
# terms in some contexts; opdracht also means "job assignment").
PROCUREMENT_TERMS = ["aanbesteding", "bestek", "lastvoorwaarden", "mededingingsprocedure", "overheidsopdracht"]


class GelinktNotulerenClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._org_name_cache: dict[str, str | None] = {}
        self._zitting_org_cache: dict[str, str | None] = {}
        self._zitting_date_cache: dict[str, str | None] = {}
        self._besluitenlijst_zitting_cache: dict[str, str | None] = {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.session.get(f"{BASE_URL}{path}", params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        time.sleep(config.REQUEST_DELAY_SECONDS)
        return resp.json()

    def search_besluiten(self, term: str, page_size: int = 100) -> list[dict]:
        """Full-text title search, paginated. Not sorted (avoids the known
        sort+include bug and isn't needed - we want everything matching)."""
        results: list[dict] = []
        page = 0
        while True:
            j = self._get("/besluiten", params={"filter[titel]": term, "page[size]": page_size, "page[number]": page})
            data = j.get("data") or []
            if not data:
                break
            results.extend(data)
            if len(data) < page_size:
                break
            page += 1
        return results

    def resolve_commune_and_date(self, besluit_id: str) -> tuple[str | None, str | None]:
        """besluit -> besluitenlijst -> zitting -> (bestuursorgaan ->
        (is-tijdsspecialisatie-van if unnamed) -> entity name) and (zitting's
        own `geplande-start` attribute, the session's real date/time - the
        besluit resource itself carries no date, only this related zitting
        does). All steps cached, since many besluiten share the same
        session/org."""
        bl = self._get(f"/besluiten/{besluit_id}/besluitenlijst")
        bl_id = (bl.get("data") or {}).get("id")
        if not bl_id:
            return None, None

        zitting_id = self._besluitenlijst_zitting_cache.get(bl_id)
        if zitting_id is None and bl_id not in self._besluitenlijst_zitting_cache:
            z = self._get(f"/besluitenlijsten/{bl_id}/zitting")
            zitting_id = (z.get("data") or {}).get("id")
            self._besluitenlijst_zitting_cache[bl_id] = zitting_id
        if not zitting_id:
            return None, None

        seance_date = self._resolve_zitting_date(zitting_id)

        org_id = self._zitting_org_cache.get(zitting_id)
        if org_id is None and zitting_id not in self._zitting_org_cache:
            org = self._get(f"/zittingen/{zitting_id}/bestuursorgaan")
            org_id = (org.get("data") or {}).get("id")
            self._zitting_org_cache[zitting_id] = org_id
        if not org_id:
            return None, seance_date

        return self._resolve_org_name(org_id), seance_date

    def _resolve_zitting_date(self, zitting_id: str) -> str | None:
        if zitting_id in self._zitting_date_cache:
            return self._zitting_date_cache[zitting_id]
        date = None
        try:
            zitting = self._get(f"/zittingen/{zitting_id}")
            date = (zitting.get("data") or {}).get("attributes", {}).get("geplande-start")
        except Exception:  # noqa: BLE001 - a missing date must not kill the whole run
            pass
        self._zitting_date_cache[zitting_id] = date
        return date

    def _resolve_org_name(self, org_id: str) -> str | None:
        if org_id in self._org_name_cache:
            return self._org_name_cache[org_id]
        name = None
        try:
            parent = self._get(f"/bestuursorganen/{org_id}/is-tijdsspecialisatie-van")
            name = (parent.get("data") or {}).get("attributes", {}).get("naam")
            if not name:
                direct = self._get(f"/bestuursorganen/{org_id}")
                name = (direct.get("data") or {}).get("attributes", {}).get("naam")
        except Exception:  # noqa: BLE001
            pass
        self._org_name_cache[org_id] = name
        return name
