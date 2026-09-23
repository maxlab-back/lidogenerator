"""Источник: Google Карты через Serper (endpoint /places).

Отдаёт организации сразу с телефоном, адресом, рубрикой, рейтингом и сайтом.
1 кредит Serper за страницу (~10 организаций). Работает для gl=ru и gl=by.
"""
from __future__ import annotations

import re

import requests

from ..models import Lead
from .search import _is_company_site, is_out_of_credits
from .website import normalize_phone


class SerperPlaces:
    name = "maps"
    URL = "https://google.serper.dev/places"

    def __init__(self, session: requests.Session, key: str):
        self.session = session
        self.key = key
        self.calls = 0          # всего потрачено запросов (= кредитов Serper)
        self.exhausted = False
        self.last_error = ""

    def search(self, query: str, gl: str = "ru", pages: int = 1) -> tuple[list[dict], int]:
        """-> (организации, потрачено запросов)."""
        out: list[dict] = []
        spent = 0
        for page in range(1, pages + 1):
            if self.exhausted:
                break
            try:
                r = self.session.post(
                    self.URL,
                    headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
                    json={"q": query, "gl": gl, "hl": "ru", "page": page},
                    timeout=25,
                )
            except requests.RequestException:
                break
            if not r.ok:
                self.exhausted = is_out_of_credits(r)
                self.last_error = f"Serper HTTP {r.status_code}: {r.text[:150]}"
                break
            spent += 1
            self.calls += 1
            try:
                places = r.json().get("places") or []
            except ValueError:
                break
            if not places:
                break
            out += places
            if len(places) < 10:
                break
        return out, spent


_TITLE_SPLIT = re.compile(r"\s*[|•·]\s*|\s[–—\-]\s|;\s+")


def _clean_title(title: str) -> str:
    """Карточки в Картах часто с SEO-хвостом: «Флагман | Диагностика, ремонт… | Замена масла»,
    «СТО У ДИМЫ — Ремонт стартеров…». Длинное название режем по сильному разделителю
    (включая «️|» после эмодзи), короткое оставляем как есть."""
    t = " ".join(title.split())
    if len(t) > 40:
        head = re.sub(r"[\s️]+$", "", _TITLE_SPLIT.split(t)[0])
        if len(head) >= 3:
            t = head
    return t[:160]


def place_to_lead(p: dict, country: str) -> Lead:
    phones = []
    n = normalize_phone(p.get("phoneNumber") or "", country)
    if n:
        phones.append(n)
    from .apify import _strip_tracking
    site = _strip_tracking((p.get("website") or "").strip())   # ?utm_…&gclid=… из рекламных карточек
    # сайт-агрегатор (localgo.by, yandex.by…) — не сайт компании, не краулим
    if site and not _is_company_site(site):
        site = ""
    rating = ""
    if p.get("rating"):
        rating = f"{p['rating']}"
        if p.get("ratingCount"):
            rating += f" ({p['ratingCount']})"
    cid = p.get("cid")
    lead = Lead(
        name=_clean_title(p.get("title") or ""),
        address=(p.get("address") or "").strip(),
        phones=phones,
        website=site,
        rubrics=[p["category"]] if p.get("category") else [],
        source="maps",
        source_url=f"https://maps.google.com/?cid={cid}" if cid else site,
        lat=p.get("latitude"),
        lon=p.get("longitude"),
        rating=rating,
        country=country,
    )
    return lead
