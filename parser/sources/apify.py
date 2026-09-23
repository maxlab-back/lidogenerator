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


# ----------------------------------------------------------------------------
# Универсальный режим: карты через Apify (Яндекс Карты, 2ГИС) — один запуск на город
# ----------------------------------------------------------------------------
MAPS_PRESETS = {
    "yandex_maps": {"actor": "zen-studio/yandex-maps-scraper", "title": "Яндекс Карты"},
    "2gis": {"actor": "m_mamaev/2gis-places-scraper", "title": "2ГИС"},
}
_SOCIAL_TYPES = {"telegram": "telegram", "vkontakte": "vk", "vk": "vk", "viber": "viber",
                 "whatsapp": "whatsapp", "instagram": "instagram", "odnoklassniki": "ok", "ok": "ok"}
_COUNTRY_CODES = {"россия": "RU", "russia": "RU", "беларусь": "BY", "belarus": "BY"}


class ApifyMaps:
    """Актор-парсер карт: принимает все фразы разом + город, отдаёт организации."""

    def __init__(self, token: str, kind: str, actor: str = "", timeout: int = 600):
        self.token = token
        self.kind = kind
        self.actor = actor or MAPS_PRESETS[kind]["actor"]
        self.timeout = timeout

    def payload(self, queries: list[str], city: str, country_name: str, max_items: int) -> dict:
        if self.kind == "yandex_maps":
            return {"query": queries, "location": f"{city}, {country_name}" if city else country_name,
                    "maxResults": max_items, "language": "ru", "includeReviews": False, "maxPhotos": 0}
        return {"query": queries, "locationQuery": city or country_name, "maxItems": max_items,
                "language": "ru", "domain": "2gis.by" if country_name == "Беларусь" else "2gis.ru",
                "includeContacts": True}

    def search(self, queries: list[str], city: str, country_name: str, max_items: int) -> list[dict]:
        url = (f"https://api.apify.com/v2/acts/{self.actor.replace('/', '~')}"
               f"/run-sync-get-dataset-items?token={self.token}")
        r = requests.post(url, json=self.payload(queries, city, country_name, max_items), timeout=self.timeout)
        if r.status_code == 402 or r.status_code == 403:
            raise RuntimeError(f"Apify отказал ({r.status_code}): актор {self.actor} требует оплаты или кредитов")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def to_lead(self, it: dict, city: str, country: str) -> Lead:
        from .website import normalize_phone
        from .search import _is_company_site

        phones: list[str] = []
        for raw in _as_list(it, "phones", "phoneValue", "phoneText", "phone"):
            for part in re.split(r"[;,]", raw):
                n = normalize_phone(part, country)
                if n and n not in phones:
                    phones.append(n)
        emails = [e for e in _as_list(it, "email", "emails") if "@" in e]
        from ..contacts import merge_socials
        socials: dict[str, list[str]] = {}
        for s in it.get("socialLinks") or []:
            if isinstance(s, dict) and s.get("url"):
                kind = _SOCIAL_TYPES.get(str(s.get("type", "")).lower())
                if kind:
                    socials = merge_socials(socials, {kind: [s["url"]]})
        site = _clean_site(_strip_tracking(_first(it, "website", "site", "url_site")))
        if site and not _is_company_site(site):
            site = ""
        rubrics = [r for r in (it.get("categories") or it.get("rubrics") or []) if isinstance(r, str)]
        if isinstance(it.get("category"), str) and it["category"] not in rubrics:
            rubrics.append(it["category"])
        rating = ""
        if it.get("rating"):
            rating = f"{float(it['rating']):.1f}"
            cnt = it.get("reviewCount") or it.get("ratingCount")
            if cnt:
                rating += f" ({cnt})"
        cc = _COUNTRY_CODES.get(str(it.get("country") or "").lower(), country)
        inn = ""
        legal = it.get("legalInfo")
        if legal:
            m = re.search(r"(?<!\d)(\d{12}|\d{10}|\d{9})(?!\d)", str(legal))
            inn = m.group(1) if m else ""
        lead = Lead(
            name=_first(it, "title", "name", "shortName")[:160],
            city=_first(it, "city") or city,
            address=_first(it, "address", "fullAddress"),
            phones=phones,
            emails=_clean(emails),
            website=site,
            rubrics=rubrics[:5],
            source=self.kind,
            source_url=_first(it, "url"),
            lat=it.get("latitude"), lon=it.get("longitude"),
            rating=rating,
            country=cc,
            inn=inn,
        )
        lead.socials = socials
        return lead


def _strip_tracking(url: str) -> str:
    """Сайты из карт приходят с рекламными метками (?utm_…&yclid=…) — отрезаем query целиком."""
    if not url:
        return ""
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


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
