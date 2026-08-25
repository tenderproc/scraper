"""HTTP client for conseilcommunal.be ("iDélibé Citoyens") - a second,
disjoint Walloon council-transparency platform from deliberations.be
(confirmed disjoint: none of its 39 communes appear in deliberations.be's
206). Unlike deliberations.be this is a real JSON REST API, no HTML
scraping needed - but it publishes only agenda point titles + structured
metadata (Matiere/Service categories), not full legal decision text like
deliberations.be's uittreksels. Endpoints found via browser network
inspection (undocumented, ASP.NET-style):
  GET /ApiCitoyen/public/v1/communes                          - roster
  GET /ApiCitoyen/public/v1/commune/{id}/seances               - session list (Points always null here)
  GET /ApiCitoyen/public/v1/commune/{id}/seance/{seanceId}     - full session incl. Points[]
"""
from __future__ import annotations

import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config

BASE_URL = "https://www.conseilcommunal.be/ApiCitoyen/public/v1"


class ConseilCommunalClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str) -> dict:
        resp = self.session.get(f"{BASE_URL}{path}", timeout=30)
        resp.raise_for_status()
        time.sleep(config.REQUEST_DELAY_SECONDS)
        return resp.json()

    def list_communes(self) -> list[dict]:
        return self._get("/communes")["Data"]

    def list_sessions(self, commune_id: int) -> list[dict]:
        data = self._get(f"/commune/{commune_id}/seances")["Data"]
        return data.get("Sessions") or []

    def get_session_detail(self, commune_id: int, session_id: int) -> dict:
        return self._get(f"/commune/{commune_id}/seance/{session_id}")["Data"]
