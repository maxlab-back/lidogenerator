"""Источник: парсер 2ГИС через Apify (обход партнёрского API 2ГИС).

Actor: m_mamaev/2gis-places-scraper — принимает ключевые слова + город, отдаёт
организации с телефонами/почтой/сайтом/рубриками.
Документация: https://apify.com/m_mamaev/2gis-places-scraper
"""
from __future__ import annotations

import re

import requests

from ..models import Lead


class ApifySource:
    name = "apify_2gis"

    def __init__(self, token: str, actor: str, queries: list[str],
                 max_items_per_city: int = 120, language: str = "ru",
                 domain: str = "2gis.ru", timeout: int = 280):
        self.token = token
        self.actor_path = actor.replace("/", "~")
        self.queries = queries
        self.max_items = max_items_per_city
        self.language = language
        self.domain = domain
        self.timeout = timeout

    def _run(self, payload: dict) -> list[dict]:
        url = (f"https://api.apify.com/v2/acts/{self.actor_path}"
               f"/run-sync-get-dataset-items?token={self.token}")
        r = requests.post(url, json=payload, timeout=self.timeout + 30)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []

    def search_city(self, city: str) -> list[Lead]:
        payload = {
            "query": self.queries,
            "locationQuery": city,
            "maxItems": self.max_items,
            "language": self.language,
            "domain": self.domain,
            "includeContacts": True,
        }
        items = self._run(payload)
        return [self._parse(it, city) for it in items]

    def _parse(self, it: dict, city: str) -> Lead:
        # реальная схема актора m_mamaev/2gis-places-scraper
        name = _first(it, "title", "shortName", "name")
        website = _first(it, "website")
        address = _first(it, "address")

        from .website import normalize_phone
        phones = []
        for raw in _as_list(it, "phoneValue", "phoneText", "phones"):
            for part in re.split(r"[;,]", raw):
                n = normalize_phone(part)
                if n and n not in phones:
                    phones.append(n)
        emails = _as_list(it, "email", "emails")

        rubrics = it.get("rubrics") or []
        if isinstance(rubrics, str):
            rubrics = [rubrics]
        cat = it.get("category")
        if cat and cat not in rubrics:
            rubrics = [*rubrics, cat]

        loc = it.get("location") or {}
        lat = loc.get("lat") if isinstance(loc, dict) else None
        lon = loc.get("lng") or loc.get("lon") if isinstance(loc, dict) else None

        lead = Lead(
            name=name,
            city=_first(it, "city") or city,
            address=address,
            phones=_clean(phones),
            emails=_clean(emails),
            website=_clean_site(website),
            rubrics=[r for r in rubrics if r],
            source=self.name,
            source_url=_first(it, "url") or website,
            region_hint=city,
            lat=lat,
            lon=lon,
        )
        lead.raw_text = " ".join([lead.name, *lead.rubrics])
        return lead


def _first(d: dict, *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for sub in ("value", "text", "name", "href", "url"):
                if isinstance(v.get(sub), str) and v[sub].strip():
                    return v[sub].strip()
    return ""


def _as_list(d: dict, *keys: str) -> list[str]:
    out: list[str] = []
    for k in keys:
        v = d.get(k)
        if not v:
            continue
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    for sub in ("value", "text", "number", "name"):
                        if isinstance(x.get(sub), str):
                            out.append(x[sub]); break
    return out


def _clean(seq: list[str]) -> list[str]:
    seen, res = set(), []
    for x in seq:
        x = x.strip()
        if x and x not in seen:
            seen.add(x); res.append(x)
    return res


def _clean_site(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url and not url.startswith("http"):
        url = "http://" + url
    return url
