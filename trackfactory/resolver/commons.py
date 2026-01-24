from __future__ import annotations

import re
from typing import Iterable

import httpx

from .model import Candidate


COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _score_title(title: str) -> float:
    score = 0.8
    lowered = title.lower()
    if "circuit" in lowered:
        score += 0.08
    if "track" in lowered:
        score += 0.05
    if "layout" in lowered or "map" in lowered:
        score += 0.04
    if re.search(r"\.svg$", lowered):
        score += 0.03
    return min(score, 1.0)


def _filter_svg_results(items: Iterable[dict]) -> list[dict]:
    results = []
    for item in items:
        imageinfo = item.get("imageinfo") or []
        if not imageinfo:
            continue
        info = imageinfo[0]
        url = info.get("url") or ""
        mime = info.get("mime") or ""
        if url.lower().endswith(".svg") or mime == "image/svg+xml":
            results.append(item)
    return results


def search_commons(query: str, limit: int = 5) -> list[Candidate]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": 6,
        "srlimit": limit,
        "format": "json",
    }
    headers = {"User-Agent": "TrackFactory/0.1 (contact: local)"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        try:
            resp = client.get(COMMONS_API, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            return []
        search_data = resp.json()

        search_results = search_data.get("query", {}).get("search", [])
        titles = [item["title"] for item in search_results]
        if not titles:
            return []

        info_params = {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "format": "json",
        }
        try:
            info_resp = client.get(COMMONS_API, params=info_params)
            info_resp.raise_for_status()
        except httpx.HTTPStatusError:
            return []
        info_data = info_resp.json()

    pages = info_data.get("query", {}).get("pages", {})
    items = list(pages.values())
    items = _filter_svg_results(items)

    candidates: list[Candidate] = []
    for item in items:
        title = item.get("title", "")
        imageinfo = item.get("imageinfo") or [{}]
        info = imageinfo[0]
        url = info.get("url", "")
        if not url:
            continue
        candidates.append(
            Candidate(
                source_type="wikimedia_svg",
                title=title,
                url=url,
                score=_score_title(title),
                payload={"imageinfo": info},
            )
        )
    return candidates
