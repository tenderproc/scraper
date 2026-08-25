"""HTML parsing for deliberations.be listing and detail pages."""
from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

_WS_RE = re.compile(r"[ \t\xa0]+")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    # the site's templates sometimes double-escape entities (e.g. "&#x27;"
    # shows up literally in the rendered text), so unescape defensively.
    text = html.unescape(html.unescape(text))
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def parse_listing_page(html: str) -> list[dict]:
    """Parse one @@faceted_query listing page into a list of card dicts.

    Each card corresponds to one agenda point (one decision or draft
    decision). Full decision text is NOT here — only the title and
    metadata shown on the card; fetch the detail page for the full text.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for card in soup.select("div.item-card"):
        link = card.select_one("a.filled-link")
        href = link.get("href") if link else None

        title_el = card.select_one("h3.card-title")
        title = _clean(title_el.get_text(" ", strip=True)) if title_el else None

        status = _clean(card.get("data-watermark"))

        meta = {}
        for row in card.select("div.item-metadata-row"):
            label_el = row.select_one("div.item-metadata-label")
            if not label_el:
                continue
            label = _clean(label_el.get_text(" ", strip=True))
            # value is whatever text/link remains in the row besides the label
            row_copy_text_parts = []
            for child in row.children:
                if child is label_el:
                    continue
                text = child.get_text(" ", strip=True) if hasattr(child, "get_text") else str(child).strip()
                if text:
                    row_copy_text_parts.append(text)
            value = _clean(" ".join(row_copy_text_parts))
            if label:
                meta[label] = value

        cards.append(
            {
                "source_url": href,
                "title": title,
                "status": status,
                "seance_date": meta.get("Séance"),
                "numero_point": meta.get("Numéro du point"),
                "matiere": meta.get("Matière"),
                "mandataire": meta.get("Mandataire"),
            }
        )
    return cards


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("li.page-item.next a") is not None


def parse_detail_page(html: str) -> str | None:
    """Extract the full decision text from a detail page.

    The page repeats the title, session/matiere/mandataire metadata block,
    then the full legal decision text (considérants + décision). We keep
    everything after the "Responsable" metadata line, which is the actual
    decision body.
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body
    if main is None:
        return None
    text = main.get_text("\n", strip=True)
    marker = "Responsable"
    idx = text.find(marker)
    if idx == -1:
        return _clean(text)
    # skip the "Responsable\n<value>" line itself
    rest = text[idx:].split("\n", 2)
    body = rest[2] if len(rest) > 2 else text[idx:]
    return _clean(body)
