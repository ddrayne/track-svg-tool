from __future__ import annotations

import re
from typing import Iterable

import httpx

from .model import Candidate


WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "TrackFactory/0.1 (https://www.trackfactor.com; mailto:info@trackfactory.com)"}


def _score_title(title: str, query: str) -> float:
    score = 0.7
    lowered = title.lower()
    query_lower = query.lower()
    if query_lower in lowered:
        score += 0.2
    tokens = [token for token in re.split(r"\W+", query_lower) if token]
    if tokens and all(token in lowered for token in tokens):
        score += 0.15
    if "road course" in lowered or "road_course" in lowered:
        score += 0.2
    if any(term in lowered for term in ("moto", "motorcycle", "kart", "drag", "flat track")):
        score -= 0.25
    if "circuit" in lowered:
        score += 0.08
    if "track" in lowered or "raceway" in lowered or "speedway" in lowered:
        score += 0.06
    if "layout" in lowered or "map" in lowered or "oval" in lowered:
        score += 0.05
    if re.search(r"\.svg$", lowered):
        score += 0.04
    if any(bad in lowered for bad in ("logo", "flag", "seal", "shield")):
        score -= 0.2
    return max(min(score, 1.0), 0.0)


def _filter_svg_titles(titles: Iterable[str]) -> list[str]:
    svg_titles = []
    for title in titles:
        if title.lower().endswith(".svg"):
            svg_titles.append(title)
    return svg_titles


def _search_pages(client: httpx.Client, query: str, limit: int) -> list[dict]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srnamespace": 0,
        "format": "json",
    }
    try:
        resp = client.get(WIKIPEDIA_API, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []
    data = resp.json()
    return data.get("query", {}).get("search", [])


def _search_files(client: httpx.Client, query: str, limit: int) -> list[str]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} filetype:svg",
        "srlimit": limit,
        "srnamespace": 6,
        "format": "json",
    }
    try:
        resp = client.get(WIKIPEDIA_API, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []
    data = resp.json()
    results = data.get("query", {}).get("search", [])
    titles: list[str] = []
    for item in results:
        title = item.get("title")
        if title and title.lower().endswith(".svg"):
            titles.append(title)
    return titles


def _fetch_page_images(client: httpx.Client, page_ids: list[int]) -> list[str]:
    if not page_ids:
        return []
    params = {
        "action": "query",
        "prop": "images",
        "pageids": "|".join(str(pid) for pid in page_ids),
        "imlimit": 50,
        "format": "json",
    }
    try:
        resp = client.get(WIKIPEDIA_API, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    titles: list[str] = []
    for page in pages.values():
        for image in page.get("images", []):
            title = image.get("title")
            if title:
                titles.append(title)
    return titles


def _fetch_page_images_for_titles(client: httpx.Client, titles: list[str]) -> list[str]:
    if not titles:
        return []
    params = {
        "action": "query",
        "prop": "images",
        "titles": "|".join(titles),
        "imlimit": 50,
        "format": "json",
    }
    try:
        resp = client.get(WIKIPEDIA_API, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    image_titles: list[str] = []
    for page in pages.values():
        for image in page.get("images", []):
            title = image.get("title")
            if title:
                image_titles.append(title)
    return image_titles


def _fetch_imageinfo(client: httpx.Client, titles: list[str]) -> list[dict]:
    if not titles:
        return []
    params = {
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "format": "json",
    }
    try:
        resp = client.get(WIKIPEDIA_API, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    return list(pages.values())


def search_wikipedia(query: str, limit: int = 5) -> list[Candidate]:
    candidates: list[Candidate] = []
    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        pages = _search_pages(client, query, limit=max(5, limit))
        page_ids = [page.get("pageid") for page in pages if page.get("pageid")]
        image_titles = _fetch_page_images(client, page_ids)
        title_variants = list(dict.fromkeys([query, query.replace("-", " "), query.replace("_", " ")]))
        image_titles.extend(_fetch_page_images_for_titles(client, title_variants))
        svg_titles = _filter_svg_titles(image_titles)
        file_titles = _search_files(client, query, limit=max(8, limit))
        combined_titles = list(dict.fromkeys(svg_titles + file_titles))
        if not combined_titles:
            return []
        info_pages = _fetch_imageinfo(client, combined_titles)

    for item in info_pages:
        title = item.get("title", "")
        imageinfo = item.get("imageinfo") or [{}]
        info = imageinfo[0]
        url = info.get("url", "")
        mime = info.get("mime", "")
        if not url:
            continue
        if mime != "image/svg+xml" and not url.lower().endswith(".svg"):
            continue
        candidates.append(
            Candidate(
                source_type="wikimedia_svg",
                title=title,
                url=url,
                score=_score_title(title, query),
                payload={"imageinfo": info},
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
