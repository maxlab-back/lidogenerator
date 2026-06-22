"""Поисковый движок: ищем сайты компаний по нишевым запросам.

Бэкенды (выбирается в config.yaml -> sources.search.backend):
  - duckduckgo : без ключа (по умолчанию). Бесплатно, но капризен к темпу.
  - google_cse : Google Custom Search JSON API (нужны GOOGLE_CSE_KEY + GOOGLE_CSE_CX). 100 запросов/день бесплатно.
  - serper     : serper.dev (нужен SERPER_API_KEY). Дёшево, результаты Google.
  - yandex_xml : Яндекс XML (нужны YANDEX_XML_USER + YANDEX_XML_KEY).
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

# домены, которые НЕ являются сайтами компаний-лидов (соцсети, маркетплейсы, карты, справочники-агрегаторы)
SKIP_DOMAINS = {
    "vk.com", "ok.ru", "instagram.com", "facebook.com", "youtube.com", "t.me",
    "wikipedia.org", "avito.ru", "ozon.ru", "wildberries.ru", "market.yandex.ru",
    "yandex.ru", "ya.ru", "google.com", "2gis.ru", "go.2gis.com", "zoon.ru",
    "yell.ru", "flamp.ru", "blizko.ru", "tiu.ru", "pulscen.ru", "satom.ru",
    "dmir.ru", "regmarkets.ru", "leroymerlin.ru", "petrovich.ru", "vseinstrumenti.ru",
    "drom.ru", "youla.ru", "rusprofile.ru", "list-org.com", "spark-interfax.ru",
}


def registered_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _is_company_site(url: str) -> bool:
    dom = registered_domain(url)
    if not dom or "." not in dom:
        return False
    return not any(dom == s or dom.endswith("." + s) for s in SKIP_DOMAINS)


# ----------------------------------------------------------------------------
# Бэкенды
# ----------------------------------------------------------------------------
class DuckDuckGoBackend:
    name = "duckduckgo"
    HTML = "https://html.duckduckgo.com/html/"
    LITE = "https://lite.duckduckgo.com/lite/"

    def __init__(self, session: requests.Session):
        self.session = session

    def search(self, query: str, limit: int) -> list[str]:
        urls = self._parse_html(query)
        if not urls:
            urls = self._parse_lite(query)
        return urls[:limit]

    def _parse_html(self, query: str) -> list[str]:
        try:
            r = self.session.post(self.HTML, data={"q": query, "kl": "ru-ru"}, timeout=20)
            r.raise_for_status()
        except requests.RequestException:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            real = _ddg_unwrap(href)
            if real:
                out.append(real)
        return out

    def _parse_lite(self, query: str) -> list[str]:
        try:
            r = self.session.post(self.LITE, data={"q": query, "kl": "ru-ru"}, timeout=20)
            r.raise_for_status()
        except requests.RequestException:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            real = _ddg_unwrap(href)
            if real and real.startswith("http"):
                out.append(real)
        return out


def _ddg_unwrap(href: str) -> str:
    if href.startswith("//duckduckgo.com/l/") or "duckduckgo.com/l/" in href:
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            return unquote(q["uddg"][0])
        return ""
    if href.startswith("http"):
        return href
    return ""


class GoogleCSEBackend:
    name = "google_cse"
    URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, session: requests.Session, key: str, cx: str):
        self.session = session
        self.key = key
        self.cx = cx

    def search(self, query: str, limit: int) -> list[str]:
        out, start = [], 1
        while len(out) < limit and start <= 91:
            params = {"key": self.key, "cx": self.cx, "q": query, "num": 10, "start": start, "gl": "ru", "hl": "ru"}
            try:
                r = self.session.get(self.URL, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException:
                break
            items = data.get("items") or []
            if not items:
                break
            out += [it.get("link", "") for it in items if it.get("link")]
            start += 10
        return out[:limit]


class SerperBackend:
    name = "serper"
    URL = "https://google.serper.dev/search"

    def __init__(self, session: requests.Session, key: str):
        self.session = session
        self.key = key

    def search(self, query: str, limit: int) -> list[str]:
        out, page = [], 1
        while len(out) < limit and page <= 5:
            try:
                r = self.session.post(
                    self.URL,
                    headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
                    json={"q": query, "gl": "ru", "hl": "ru", "num": 10, "page": page},
                    timeout=20,
                )
                r.raise_for_status()
                data = r.json()
            except requests.RequestException:
                break
            organic = data.get("organic") or []
            if not organic:
                break
            out += [o.get("link", "") for o in organic if o.get("link")]
            page += 1
        return out[:limit]


class YandexXmlBackend:
    name = "yandex_xml"
    URL = "https://yandex.ru/search/xml"

    def __init__(self, session: requests.Session, user: str, key: str):
        self.session = session
        self.user = user
        self.key = key

    def search(self, query: str, limit: int) -> list[str]:
        out, page = [], 0
        while len(out) < limit and page < 5:
            params = {"folderid": self.user, "apikey": self.key, "query": query,
                      "l10n": "ru", "page": page, "groupby": "attr=d.mode=deep.groups-on-page=20.docs-in-group=1"}
            try:
                r = self.session.get(self.URL, params=params, timeout=20)
                r.raise_for_status()
            except requests.RequestException:
                break
            urls = re.findall(r"<url>(.*?)</url>", r.text)
            if not urls:
                break
            out += urls
            page += 1
        return out[:limit]


def build_backend(cfg: dict, keys: dict, session: requests.Session):
    name = (cfg.get("backend") or "duckduckgo").lower()
    if name == "google_cse":
        if keys.get("google_cse_key") and keys.get("google_cse_cx"):
            return GoogleCSEBackend(session, keys["google_cse_key"], keys["google_cse_cx"])
        print("⚠️  google_cse выбран, но нет GOOGLE_CSE_KEY/CX — откат на DuckDuckGo.")
    elif name == "serper":
        if keys.get("serper"):
            return SerperBackend(session, keys["serper"])
        print("⚠️  serper выбран, но нет SERPER_API_KEY — откат на DuckDuckGo.")
    elif name == "yandex_xml":
        if keys.get("yandex_xml_user") and keys.get("yandex_xml_key"):
            return YandexXmlBackend(session, keys["yandex_xml_user"], keys["yandex_xml_key"])
        print("⚠️  yandex_xml выбран, но нет YANDEX_XML_USER/KEY — откат на DuckDuckGo.")
    return DuckDuckGoBackend(session)


# ----------------------------------------------------------------------------
# Discover: запросы × регионы -> уникальные домены компаний
# ----------------------------------------------------------------------------
def discover(backend, queries: list[str], regions: list[str], cfg: dict,
             user_agent: str) -> list[tuple[str, str]]:
    """Возвращает список (url, city_hint) уникальных доменов компаний."""
    per_query = cfg.get("results_per_query", 25)
    max_sites = cfg.get("max_sites", 400)
    delay = cfg.get("request_delay", 2.0)

    seen_domains: set[str] = set()
    results: list[tuple[str, str]] = []

    # пары (запрос, город-подсказка). regions пуст -> один общероссийский проход.
    pairs: list[tuple[str, str]] = []
    region_list = regions if regions else [""]
    for region in region_list:
        for q in queries:
            full = f"{q} {region}".strip()
            pairs.append((full, region))

    total = len(pairs)
    for i, (q, hint) in enumerate(pairs, 1):
        if len(results) >= max_sites:
            break
        try:
            urls = backend.search(q, per_query)
        except Exception as e:
            print(f"   ! поиск упал [{q}]: {e}")
            urls = []
        new = 0
        for u in urls:
            if not _is_company_site(u):
                continue
            dom = registered_domain(u)
            if dom in seen_domains:
                continue
            seen_domains.add(dom)
            results.append((f"https://{dom}", hint))
            new += 1
            if len(results) >= max_sites:
                break
        print(f"  [{i}/{total}] «{q}» -> {len(urls)} ссылок, +{new} новых доменов "
              f"(всего {len(results)})")
        time.sleep(delay)

    if not results:
        print("⚠️  Поиск не дал результатов. Вероятно, бэкенд ограничил темп. "
              "Увеличь sources.search.request_delay или подключи ключ (Serper/Google CSE).")
    return results
