"""Thin HTTP client for deliberations.be (a Plone / iA.Délib site — plain
server-rendered HTML fragments, no JSON API for decision content)."""
from __future__ import annotations

import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config


class DeliberationsClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp

    def fetch_listing_page(self, commune_slug: str, b_start: int) -> str:
        url = f"{config.BASE_URL}/{commune_slug}/decisions/@@faceted_query"
        resp = self._get(url, params={"b_start": b_start})
        time.sleep(config.REQUEST_DELAY_SECONDS)
        return resp.text

    def fetch_detail_page(self, url: str) -> str:
        resp = self._get(url)
        time.sleep(config.REQUEST_DELAY_SECONDS)
        return resp.text
