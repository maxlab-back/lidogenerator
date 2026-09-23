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

from ..htmlutil import parse_html

# домены, которые НЕ являются сайтами компаний-лидов (соцсети, маркетплейсы, карты, справочники-агрегаторы)
SKIP_DOMAINS = {
    "vk.com", "ok.ru", "instagram.com", "facebook.com", "youtube.com", "t.me",
    "wikipedia.org", "avito.ru", "ozon.ru", "wildberries.ru", "market.yandex.ru",
    "yandex.ru", "ya.ru", "google.com", "2gis.ru", "go.2gis.com", "zoon.ru",
    "yell.ru", "flamp.ru", "blizko.ru", "tiu.ru", "pulscen.ru", "satom.ru",
    "dmir.ru", "regmarkets.ru", "leroymerlin.ru", "petrovich.ru", "vseinstrumenti.ru",
    "drom.ru", "youla.ru", "rusprofile.ru", "list-org.com", "spark-interfax.ru",
    # Беларусь: карты, объявления, справочники
    "yandex.by", "2gis.by", "kufar.by", "onliner.by", "deal.by", "relax.by", "localgo.by",
    "avtoportal.by", "103.by", "rabota.by", "ozon.by", "wildberries.by",
    # вакансии, справочники, отзовики, медиа, соцсети
    "hh.ru", "rabota.ru", "superjob.ru", "profi.ru", "uslugi.yandex.ru", "orgpage.ru",
    "spravker.ru", "cataloxy.ru", "yp.ru", "checko.ru", "zachestnyibiznes.ru", "dzen.ru",
    "pikabu.ru", "otzovik.com", "irecommend.ru", "tiktok.com", "pinterest.com",
    "twitter.com", "x.com", "livejournal.com", "google.ru", "google.by", "yandex.com",
    "mail.ru", "rbc.ru", "tripadvisor.ru", "tripadvisor.com", "booking.com",
    "prodoctorov.ru", "napopravku.ru", "lemanapro.ru", "gov.ru", "gov.by",
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


def is_out_of_credits(r: requests.Response) -> bool:
    """Serper отвечает 4xx с текстом про кредиты/баланс, когда лимит исчерпан."""
    return r.status_code in (400, 401, 402, 403) and bool(
        re.search(r"credit|balance|not enough|quota|top.?up", r.text[:500], re.I))


def serper_balance(key: str) -> int | None:
    """Остаток кредитов Serper (None — не удалось узнать)."""
    try:
        r = requests.get("https://google.serper.dev/account", headers={"X-API-KEY": key}, timeout=10)
        r.raise_for_status()
        return int(r.json().get("balance"))
    except (requests.RequestException, ValueError, TypeError):
        return None


def search_items(backend, query: str, limit: int, gl: str = "ru") -> list[dict]:
    """Выдача как [{link, title, snippet}]. Бэкенды без сниппетов отдают только ссылки."""
    if hasattr(backend, "search_items"):
        return backend.search_items(query, limit, gl)
    return [{"link": u, "title": "", "snippet": ""} for u in backend.search(query, limit)]


# ----------------------------------------------------------------------------
# Бэкенды
# ----------------------------------------------------------------------------
class DuckDuckGoBackend:
    """Бесплатный поиск без ключа. Капризен к темпу: при частых запросах отдаёт пустую
    страницу или заглушку «anomaly» — это ловится и уходит в журнал, а не молчит."""

    name = "duckduckgo"
    HTML = "https://html.duckduckgo.com/html/"
    LITE = "https://lite.duckduckgo.com/lite/"
    THROTTLED = ("anomaly", "unusual traffic", "are you a robot", "blocked")

    def __init__(self, session: requests.Session):
        self.session = session
        self.calls = 0          # бесплатно, но счётчик нужен для паузы между запросами
        self.last_error = ""
        self.throttled = 0      # сколько запросов подряд упёрлись в ограничение темпа

    def search(self, query: str, limit: int, gl: str = "ru") -> list[str]:
        return [it["link"] for it in self.search_items(query, limit, gl)]

    def search_items(self, query: str, limit: int, gl: str = "ru") -> list[dict]:
        self.calls += 1
        items = self._items(self.HTML, query, self._parse_results)
        if not items:
            items = self._items(self.LITE, query, self._parse_lite)
        if not items and self.last_error:
            time.sleep(3)       # один вежливый повтор: чаще всего это временный троттлинг
            items = self._items(self.HTML, query, self._parse_results)
        if items:
            self.throttled = 0
        elif self.last_error:
            self.throttled += 1
            if self.throttled == 3:
                self.last_error = ("DuckDuckGo ограничивает темп третий запрос подряд — выдача пустая. "
                                   "Увеличь sources.search.request_delay в config.yaml или пополни Serper.")
        return items[:limit]

    def _items(self, url: str, query: str, parse) -> list[dict]:
        try:
            r = self.session.post(url, data={"q": query, "kl": "ru-ru"}, timeout=20)
        except requests.RequestException as e:
            self.last_error = f"DuckDuckGo недоступен: {e}"
            return []
        body = r.text[:3000].lower()
        if r.status_code in (202, 403, 429) or any(m in body for m in self.THROTTLED):
            self.last_error = f"DuckDuckGo ограничил темп запросов (HTTP {r.status_code}) — выдача пустая"
            return []
        if not r.ok:
            self.last_error = f"DuckDuckGo HTTP {r.status_code}"
            return []
        out = parse(r.text)
        if out:
            self.last_error = ""
        return out

    @staticmethod
    def _parse_results(html: str) -> list[dict]:
        soup = parse_html(html)
        out = []
        for a in soup.select("a.result__a"):
            link = _ddg_unwrap(a.get("href", ""))
            if not link:
                continue
            block = a.find_parent(class_="result") or a.parent
            snippet = block.select_one(".result__snippet") if block else None
            out.append({"link": link, "title": a.get_text(" ", strip=True),
                        "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
        return out

    @staticmethod
    def _parse_lite(html: str) -> list[dict]:
        soup = parse_html(html)
        out = []
        for a in soup.select("a.result-link") or soup.find_all("a", href=True):
            link = _ddg_unwrap(a.get("href", ""))
            if not link or not link.startswith("http"):
                continue
            row = a.find_parent("tr")
            snippet = row.find_next("td", class_="result-snippet") if row else None
            out.append({"link": link, "title": a.get_text(" ", strip=True),
                        "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
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
    """Google Custom Search JSON API: 100 запросов в день бесплатно. Запасной вариант,
    когда кончились кредиты Serper, — выдача та же гугловская, со сниппетами."""

    name = "google_cse"
    URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, session: requests.Session, key: str, cx: str):
        self.session = session
        self.key = key
        self.cx = cx
        self.calls = 0
        self.exhausted = False   # дневная бесплатная квота исчерпана
        self.last_error = ""

    def search(self, query: str, limit: int, gl: str = "ru") -> list[str]:
        return [it["link"] for it in self.search_items(query, limit, gl)]

    def search_items(self, query: str, limit: int, gl: str = "ru") -> list[dict]:
        out: list[dict] = []
        start = 1
        while len(out) < limit and start <= 91 and not self.exhausted:
            params = {"key": self.key, "cx": self.cx, "q": query, "num": 10, "start": start,
                      "gl": gl, "hl": "ru"}
            try:
                r = self.session.get(self.URL, params=params, timeout=20)
            except requests.RequestException as e:
                self.last_error = f"Google CSE недоступен: {e}"
                break
            if not r.ok:
                if r.status_code in (403, 429) and re.search(r"limit|quota", r.text[:500], re.I):
                    self.exhausted = True
                    self.last_error = "Google CSE: бесплатные 100 запросов на сегодня кончились"
                else:
                    self.last_error = f"Google CSE HTTP {r.status_code}: {r.text[:150]}"
                break
            self.calls += 1
            try:
                items = (r.json().get("items") or [])
            except ValueError:
                break
            if not items:
                break
            out += [{"link": it.get("link", ""), "title": it.get("title", ""),
                     "snippet": it.get("snippet", "")} for it in items if it.get("link")]
            start += 10
        return out[:limit]


class SerperBackend:
    name = "serper"
    URL = "https://google.serper.dev/search"

    def __init__(self, session: requests.Session, key: str):
        self.session = session
        self.key = key
        self.calls = 0          # потрачено запросов (= кредитов Serper)
        self.exhausted = False  # кредиты кончились — вызывающий переключится на бесплатный поиск
        self.last_error = ""

    def search(self, query: str, limit: int, gl: str = "ru") -> list[str]:
        return [it["link"] for it in self.search_items(query, limit, gl)]

    def search_items(self, query: str, limit: int, gl: str = "ru") -> list[dict]:
        out, page = [], 1
        while len(out) < limit and page <= 5 and not self.exhausted:
            try:
                r = self.session.post(
                    self.URL,
                    headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
                    json={"q": query, "gl": gl, "hl": "ru", "num": 10, "page": page},
                    timeout=20,
                )
            except requests.RequestException:
                break
            if not r.ok:
                self.exhausted = self.exhausted or is_out_of_credits(r)
                self.last_error = f"Serper HTTP {r.status_code}: {r.text[:150]}"
                break
            self.calls += 1
            try:
                data = r.json()
            except ValueError:
                break
            organic = data.get("organic") or []
            if not organic:
                break
            out += [{"link": o["link"], "title": o.get("title", ""), "snippet": o.get("snippet", "")}
                    for o in organic if o.get("link")]
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


def free_backend(session: requests.Session, keys: dict, after=None):
    """Бесплатная ступень поиска, когда платная кончилась.

    Google CSE (100 запросов в день) лучше DuckDuckGo: та же выдача Google и сниппеты.
    `after` — движок, который только что выдохся: на него же не возвращаемся.
    """
    used = getattr(after, "name", "")
    if used != "google_cse" and keys.get("google_cse_key") and keys.get("google_cse_cx"):
        return GoogleCSEBackend(session, keys["google_cse_key"], keys["google_cse_cx"])
    return DuckDuckGoBackend(session)


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
