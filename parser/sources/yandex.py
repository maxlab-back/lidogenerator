"""Яндекс Поиск API v2 (Yandex Cloud / AI Studio): выдача Яндекса по России и Беларуси.

По РФ выдача Яндекса заметно полнее Google для локального бизнеса.
.env:
  YANDEX_SEARCH_API_KEY — API-ключ сервисного аккаунта с ролью search-api.webSearch.user
  YANDEX_FOLDER_ID      — идентификатор каталога Yandex Cloud
Документация: https://aistudio.yandex.ru/docs/ru/search-api/api-ref/WebSearch/search
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import requests


class YandexSearchBackend:
    name = "yandex"
    URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
    REGION = {"ru": "225", "by": "149"}                       # id регионов Яндекса: Россия, Беларусь
    SEARCH_TYPE = {"ru": "SEARCH_TYPE_RU", "by": "SEARCH_TYPE_BE"}

    def __init__(self, session: requests.Session, api_key: str, folder_id: str):
        self.session = session
        self.api_key = api_key
        self.folder_id = folder_id
        self.calls = 0
        self.last_error = ""

    def search(self, query: str, limit: int, gl: str = "ru") -> list[str]:
        return [it["link"] for it in self.search_items(query, limit, gl)]

    def search_items(self, query: str, limit: int, gl: str = "ru") -> list[dict]:
        out: list[dict] = []
        page = 0
        while len(out) < limit and page < 5:
            body = {
                "query": {"searchType": self.SEARCH_TYPE.get(gl, "SEARCH_TYPE_RU"), "queryText": query,
                          "page": str(page), "familyMode": "FAMILY_MODE_MODERATE"},
                "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": "10", "docsInGroup": "1"},
                "maxPassages": "2",
                "region": self.REGION.get(gl, "225"),
                "l10n": "LOCALIZATION_RU",
                "folderId": self.folder_id,
                "responseFormat": "FORMAT_XML",
            }
            try:
                r = self.session.post(self.URL, json=body, timeout=30,
                                      headers={"Authorization": f"Api-Key {self.api_key}"})
                self.calls += 1
                if not r.ok:
                    self.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    break
                docs = parse_xml(_decode(r.json().get("rawData", "")))
            except (requests.RequestException, ValueError) as e:
                self.last_error = str(e)
                break
            if not docs:
                break
            out += docs
            page += 1
        return out[:limit]


def _decode(raw: str) -> str:
    """rawData — XML в base64 (bytes в JSON-маппинге protobuf); на всякий случай принимаем и сырой."""
    if not raw:
        return ""
    if raw.lstrip().startswith("<"):
        return raw
    try:
        return base64.b64decode(raw).decode("utf-8", "ignore")
    except (ValueError, TypeError):
        return ""


def parse_xml(xml: str) -> list[dict]:
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for doc in root.iter("doc"):
        url = (doc.findtext("url") or "").strip()
        if not url:
            continue
        t = doc.find("title")
        title = "".join(t.itertext()).strip() if t is not None else ""
        snippet = " ".join("".join(p.itertext()).strip() for p in doc.iter("passage"))
        if not snippet:
            h = doc.find("headline")
            snippet = "".join(h.itertext()).strip() if h is not None else ""
        out.append({"link": url, "title": title, "snippet": snippet})
    return out
