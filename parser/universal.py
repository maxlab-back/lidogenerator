"""Универсальный поиск лидов: слова + контекст + сфера + страна/регионы/города.

Поток задачи:
  1. план: (фраза × город) для выбранных регионов РФ/РБ;
  2. поиск: Google Карты (Serper), Яндекс Карты и 2ГИС (Apify, один запуск на город),
     выдача Google и Яндекса; склейка по домену. Ответы платных API кешируются в базе;
  3. обход сайтов: контакты, мессенджеры, город, текст; JS-сайты — через Chromium;
     почты проверяются по DNS (MX);
  4. релевантность по словам (relevance.Profile), затем ИИ-проверка по смыслу (ai.AI);
  5. юр.данные DaData для РФ, отсев ликвидированных;
  6. склейка дублей, запись в базу (новые / уже известные), Excel, уведомление в Telegram.
Задача крутится в фоновом потоке, прогресс и лиды читает веб-интерфейс.
"""
from __future__ import annotations

import math
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from . import geo
from .cities import detect_city
from .contacts import filter_emails, merge_socials
from .dedupe import merge_leads
from .export import rows_to_xlsx
from .models import Lead
from .relevance import Profile
from .sources.apify import MAPS_PRESETS, ApifyMaps
from .sources.maps import SerperPlaces, place_to_lead
from .contacts import tg_username
from .sources.search import (_is_company_site, build_backend, free_backend, registered_domain,
                             search_items, serper_balance)
from .sources.telegram import channel_to_lead, fetch_channel
from .sources.website import enrich_one
from .sources.yandex import YandexSearchBackend

SOURCE_TITLES = {"maps": "Google Карты", "yandex_maps": "Яндекс Карты", "2gis": "2ГИС",
                 "search": "Google", "yandex": "Яндекс", "telegram": "Telegram"}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, type(default)(v)))
    except (TypeError, ValueError):
        return default


def _lines(v, commas: bool = True) -> list[str]:
    """Список из JSON-массива или строки (по строкам; commas=True — ещё и по запятым)."""
    if isinstance(v, str):
        v = (v.replace(",", "\n") if commas else v).split("\n")
    return [s.strip() for s in (v or []) if isinstance(s, str) and s.strip()]


def _flag(d: dict, key: str, default: bool, legacy: str = "") -> bool:
    if key in d:
        return bool(d[key])
    if legacy and legacy in d:
        return bool(d[legacy])
    return default


@dataclass
class Spec:
    keywords: list[str]
    context: str = ""
    minus: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=lambda: ["RU"])
    regions: list[str] = field(default_factory=list)      # "RU:Московская область"
    coverage: str = "centers"                             # centers | all
    custom_cities: list[str] = field(default_factory=list)
    maps_google: bool = True
    maps_yandex: bool = False
    maps_2gis: bool = False
    web_google: bool = True
    web_yandex: bool = False
    web_telegram: bool = False
    crawl: bool = True
    ai: bool = True
    legal: bool = True
    check_email: bool = True
    use_cache: bool = True
    notify: bool = False
    per_query: int = 20
    maps_pages: int = 1
    apify_max: int = 50
    max_sites: int = 300
    ai_max: int = 200
    credit_limit: int = 0          # потолок кредитов Serper на поиск (0 — без ограничения)
    threshold: float = 0.5
    sphere: str = ""
    title: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Spec":
        kws = _lines(d.get("keywords"), commas=False)   # фраза запроса может содержать запятую
        countries = [c for c in (d.get("countries") or []) if c in geo.COUNTRIES] or ["RU"]
        regions = [r for r in (d.get("regions") or []) if isinstance(r, str) and ":" in r]
        return cls(
            keywords=kws[:30],
            context=str(d.get("context") or "")[:2000],
            minus=_lines(d.get("minus"))[:50],
            countries=countries,
            regions=regions,
            coverage="all" if d.get("coverage") == "all" else "centers",
            custom_cities=_lines(d.get("custom_cities"))[:100],
            maps_google=_flag(d, "maps_google", True, "use_maps"),
            maps_yandex=_flag(d, "maps_yandex", False),
            maps_2gis=_flag(d, "maps_2gis", False),
            web_google=_flag(d, "web_google", True, "use_web"),
            web_yandex=_flag(d, "web_yandex", False),
            web_telegram=_flag(d, "web_telegram", False),
            crawl=_flag(d, "crawl", True),
            ai=_flag(d, "ai", True),
            legal=_flag(d, "legal", True),
            check_email=_flag(d, "check_email", True),
            use_cache=_flag(d, "use_cache", True),
            notify=_flag(d, "notify", False),
            per_query=_clamp(d.get("per_query"), 10, 50, 20),
            maps_pages=_clamp(d.get("maps_pages"), 1, 5, 1),
            apify_max=_clamp(d.get("apify_max"), 10, 500, 50),
            max_sites=_clamp(d.get("max_sites"), 10, 3000, 300),
            ai_max=_clamp(d.get("ai_max"), 0, 3000, 200),
            credit_limit=_clamp(d.get("credit_limit"), 0, 100000, 0),
            threshold=_clamp(d.get("threshold"), 0.0, 1.0, 0.5),
            sphere=str(d.get("sphere") or ""),
            title=str(d.get("title") or "")[:80],
        )

    @property
    def any_source(self) -> bool:
        return any((self.maps_google, self.maps_yandex, self.maps_2gis, self.web_google, self.web_yandex,
                    self.web_telegram))


def plan(spec: Spec) -> list[tuple[str, str, str, str]]:
    """-> [(запрос, город, регион, страна)]. Нет регионов/городов -> проход по стране целиком."""
    places: list[tuple[str, str, str]] = []
    for rk in spec.regions:
        cc, reg = rk.split(":", 1)
        country = geo.COUNTRIES.get(cc)
        if not country:
            continue
        for name, cities in country["regions"]:
            if name == reg:
                chosen = cities[:1] if spec.coverage == "centers" else cities
                places += [(c, reg, cc) for c in chosen]
    for city in spec.custom_cities:
        cc, reg = geo.locate(city)
        places.append((city, reg, cc or spec.countries[0]))
    if not places:
        places = [("", "", cc) for cc in spec.countries]

    seen: set[tuple[str, str]] = set()
    tasks = []
    for city, reg, cc in places:
        if (city, cc) in seen:
            continue
        seen.add((city, cc))
        for kw in spec.keywords:
            tasks.append((f"{kw} {city}".strip(), city, reg, cc))
    return tasks


def serper_credits(spec: Spec, tasks: int, web: bool, maps: bool, telegram: bool) -> int:
    """Сколько кредитов Serper съест план: страница выдачи и страница карт — по кредиту."""
    per_page = math.ceil(spec.per_query / 10)
    return tasks * ((per_page if web else 0) + (spec.maps_pages if maps else 0)
                    + (per_page if telegram else 0))


def estimate(spec: Spec, caps: dict, ucfg: dict, balance: int | None = None) -> dict:
    """Верхняя оценка расходов (кеш делает реальные траты меньше)."""
    tasks = plan(spec)
    n = len(tasks)
    cities = len({(c, cc) for _, c, _, cc in tasks})
    on_serper = caps.get("backend") == "serper"
    serper = serper_credits(spec, n, spec.web_google and on_serper,
                            spec.maps_google and bool(caps.get("serper")),
                            spec.web_telegram and on_serper)
    yandex = n * math.ceil(spec.per_query / 10) if spec.web_yandex and caps.get("yandex") else 0
    prices = (ucfg.get("apify") or {}).get("price_per_1000") or {}
    apify = 0.0
    if caps.get("apify"):
        if spec.maps_yandex:
            apify += cities * spec.apify_max * float(prices.get("yandex_maps", 7)) / 1000
        if spec.maps_2gis:
            apify += cities * spec.apify_max * float(prices.get("2gis", 5)) / 1000
    ai_usd = 0.0
    if spec.ai and caps.get("ai"):
        a = ucfg.get("ai") or {}
        per_call = (3500 * float(a.get("price_in", 5)) + 700 * float(a.get("price_out", 25))) / 1_000_000
        ai_usd = per_call * spec.ai_max
    out = {"tasks": n, "cities": cities, "credits": serper, "yandex_calls": yandex,
           "apify_usd": round(apify, 2), "ai_usd": round(ai_usd, 2),
           "free_backend": "Google CSE" if caps.get("google_cse") else "DuckDuckGo"}
    per_task = serper / max(1, n)
    if spec.credit_limit and serper > spec.credit_limit:
        out["credits"] = spec.credit_limit      # дальше лимита Serper тратить не станем
        out["limited_tasks"] = int(spec.credit_limit // per_task) if per_task else n
    if balance is not None and serper:
        out.update(balance=balance, enough=out["credits"] <= balance,
                   covered_tasks=min(n, int(balance // per_task)))
    return out


# ----------------------------------------------------------------------------
# Задача
# ----------------------------------------------------------------------------
class Job:
    def __init__(self, spec: Spec, cfg: dict, db, user: str = "", schedule_id: int | None = None,
                 renderer=None):
        self.id = uuid.uuid4().hex[:8]
        self.spec = spec
        self.cfg = cfg
        self.db = db
        self.user = user
        self.schedule_id = schedule_id
        self.renderer = renderer
        self.title = spec.title or (spec.keywords[0] if spec.keywords else "поиск")
        self.status = "running"          # running | done | cancelled | error
        self.stage = "Подготовка"
        self.done = 0
        self.total = 0
        self.credits = 0                 # Serper
        self.yandex_calls = 0
        self.apify_items: dict[str, int] = {}
        self.apify_cost = 0.0
        self.ai_cost = 0.0
        self.cache_hits = 0
        self.new_count = 0
        self.error = ""
        self.files: list[str] = []
        self.started = time.time()
        self.finished: float | None = None
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self._log: list[str] = []
        self._rows: list[dict] = []
        self.final = False               # строки уже из базы (можно ставить статусы)
        self.version = 0

    def say(self, msg: str) -> None:
        with self._lock:
            self._log.append(f"{datetime.now():%H:%M:%S}  {msg}")

    def publish(self, lead: Lead) -> None:
        with self._lock:
            self._rows.append(lead_json(lead))
            self.version += 1

    def replace_rows(self, rows: list[dict], final: bool = False) -> None:
        with self._lock:
            self._rows = rows
            self.final = final
            self.version += 1

    def rows(self) -> list[dict]:
        with self._lock:
            return list(self._rows)

    def state(self, log_from: int = 0) -> dict:
        with self._lock:
            rows = list(self._rows)
            log = self._log[log_from:]
            log_next = len(self._log)
        thr = self.spec.threshold
        return {
            "id": self.id, "title": self.title, "status": self.status, "stage": self.stage,
            "done": self.done, "total": self.total, "error": self.error, "files": self.files,
            "version": self.version, "final": self.final, "threshold": thr, "user": self.user,
            "elapsed": round((self.finished or time.time()) - self.started),
            "costs": {"serper": self.credits, "yandex": self.yandex_calls,
                      "apify_usd": round(self.apify_cost, 3), "ai_usd": round(self.ai_cost, 3),
                      "cache_hits": self.cache_hits},
            "log": log, "log_next": log_next,
            "counts": {
                "all": len(rows),
                "target": sum(1 for r in rows if (r.get("score") or 0) >= thr),
                "new": sum(1 for r in rows if r.get("is_new")),
                "phones": sum(1 for r in rows if r.get("phones")),
                "emails": sum(1 for r in rows if r.get("emails")),
            },
        }


DB_FIELDS = ("name", "country", "region", "city", "address", "website", "phones", "emails", "socials",
             "rubrics", "rating", "description", "inn", "ogrn", "legal_name", "okved", "manager",
             "legal_status", "employees", "revenue", "ai_verdict", "ai_type", "ai_reason",
             "ai_confidence", "ai_services", "score", "source", "source_url")


def lead_dict(l: Lead) -> dict:
    return {f: getattr(l, f) for f in DB_FIELDS}


def lead_json(l: Lead) -> dict:
    """Строка для интерфейса, пока задача идёт (до записи в базу — без id и статусов)."""
    d = lead_dict(l)
    d.update(id=None, query=l.query, matched=l.matched_keywords, is_new=None, asked=l.region_hint,
             status="", owner="", comment="", crm_id="")
    return d


# ----------------------------------------------------------------------------
# Выполнение задачи
# ----------------------------------------------------------------------------
def run_job(job: Job) -> None:
    job.db.start_run(job.id, job.title, asdict(job.spec), job.user, job.schedule_id)
    try:
        _run(job)
        job.status = "cancelled" if job.cancel.is_set() else "done"
    except Exception as e:  # noqa: BLE001 — любая ошибка должна дойти до интерфейса
        job.status = "error"
        job.error = str(e)
        job.say("ОШИБКА: " + "".join(traceback.format_exception_only(type(e), e)).strip())
    finally:
        job.finished = time.time()
        job.stage = {"done": "Готово", "cancelled": "Остановлено",
                     "error": "Ошибка"}.get(job.status, job.stage)
        job.db.finish_run(job.id, status=job.status, credits=job.credits,
                          ai_cost=round(job.ai_cost + job.apify_cost, 4),
                          total=len(job.rows()), new=job.new_count)


def _run(job: Job) -> None:
    spec, cfg, db = job.spec, job.cfg, job.db
    keys = cfg.get("_keys", {})
    ucfg = cfg.get("universal", {})
    ua = cfg.get("network", {}).get("user_agent", "Mozilla/5.0")
    search_cfg = cfg.get("sources", {}).get("search", {})
    we = cfg.get("sources", {}).get("website_enrich", {})
    delay = float(search_cfg.get("request_delay", 1.0))
    cache_ttl = float(ucfg.get("cache_days", 7)) * 86400
    proxies = [p for p in (ucfg.get("proxies") or []) if p]

    session = requests.Session()
    session.headers.update({"User-Agent": ua})
    profile = Profile(spec.keywords, spec.context, spec.minus)
    tasks = plan(spec)
    countries = sorted({cc for *_, cc in tasks})
    city_pool = geo.all_cities(countries)
    city_pool += [c for c in spec.custom_cities if c not in city_pool]

    def cached(key: str, fn):
        if spec.use_cache:
            v = db.cache_get(key, cache_ttl)
            if v is not None:
                job.cache_hits += 1
                return v
        v = fn()
        if v:
            db.cache_set(key, v)   # пустое не кешируем — это могла быть ошибка
        return v

    # ---- источники ----
    engines = []
    if spec.web_google:
        engines.append(("search", build_backend(search_cfg, keys, session)))
    if spec.web_yandex:
        if keys.get("yandex_search_key") and keys.get("yandex_folder"):
            engines.append(("yandex", YandexSearchBackend(session, keys["yandex_search_key"], keys["yandex_folder"])))
        else:
            job.say("⚠ Яндекс Поиск: нет YANDEX_SEARCH_API_KEY / YANDEX_FOLDER_ID — источник пропущен.")
    places = None
    if spec.maps_google:
        if keys.get("serper"):
            places = SerperPlaces(session, keys["serper"])
        else:
            job.say("⚠ Google Карты работают через Serper — нет SERPER_API_KEY, источник пропущен.")
    apify_kinds = [k for k, on in (("yandex_maps", spec.maps_yandex), ("2gis", spec.maps_2gis)) if on]
    if apify_kinds and not keys.get("apify"):
        job.say("⚠ Яндекс Карты / 2ГИС работают через Apify — нет APIFY_TOKEN, источник пропущен.")
        apify_kinds = []
    tg_backend = None
    if spec.web_telegram:
        # каналы ищем тем же поисковиком, что и сайты Google (Serper или бесплатный DuckDuckGo)
        tg_backend = next((b for n, b in engines if n == "search"), None) or build_backend(search_cfg, keys, session)

    # ---- кредиты Serper: сверяем смету с остатком ДО старта ----
    def on_serper(b) -> bool:
        return getattr(b, "name", "") == "serper"

    def drop_serper(reason: str, topup: bool = True) -> None:
        """Снимаем поиск с Serper: карты выключаются, сайты и каналы уходят на бесплатное."""
        nonlocal places, engines, tg_backend
        free = free_backend(session, keys)
        lost = []
        if places is not None:
            lost.append("Google Карты отключены (это обычно половина лидов и большая часть телефонов)")
            places = None
        if any(on_serper(b) for _, b in engines):
            lost.append(f"сайты ищу через {free.name}")
            engines = [(n, free if on_serper(b) else b) for n, b in engines]
        if tg_backend is not None and on_serper(tg_backend):
            lost.append("Telegram-каналы тоже через него")
            tg_backend = free
        job.say(f"⚠ {reason}. " + ("; ".join(lost) if lost else "Платных источников в этом поиске нет")
                + (". Пополнить: serper.dev" if topup else ""))

    serper_used = bool(keys.get("serper")) and (places is not None
                                                or any(on_serper(b) for _, b in engines)
                                                or (tg_backend is not None and on_serper(tg_backend)))
    if serper_used:
        need = serper_credits(spec, len(tasks), any(on_serper(b) for _, b in engines),
                              places is not None, tg_backend is not None and on_serper(tg_backend))
        planned = min(need, spec.credit_limit) if spec.credit_limit else need   # потолок трат тоже считается
        per_task = max(1.0, need / max(1, len(tasks)))
        bal = serper_balance(keys["serper"])
        if bal == 0:
            drop_serper("Serper: кредиты закончились")
        elif bal is None:
            job.say(f"⚠ Serper: остаток узнать не удалось. Смета этого поиска — до {planned} кредитов.")
        elif planned > bal:
            job.say(f"⚠ Serper: смете нужно до {planned} кредитов, на счету {bal}. Хватит примерно на "
                    f"{int(bal // per_task)} из {len(tasks)} запросов, дальше сам переключусь на "
                    f"{free_backend(session, keys).name} и выключу Google Карты. Пополнить: serper.dev")
        else:
            job.say(f"Serper: потрачу не больше {planned} кредитов, на счету {bal} "
                    f"(останется около {bal - planned})"
                    + (f"; потолок на этот поиск — {spec.credit_limit} кр." if spec.credit_limit else "")
                    + (f": на Serper пройдут первые ~{int(spec.credit_limit // per_task)} из "
                       f"{len(tasks)} запросов, остальные — бесплатно"
                       if spec.credit_limit and spec.credit_limit < need else ""))
    if not (engines or places or apify_kinds or tg_backend):
        raise RuntimeError("Не выбран ни один доступный источник.")
    names = ([SOURCE_TITLES["maps"]] if places else []) + [MAPS_PRESETS[k]["title"] for k in apify_kinds] \
        + [f"{SOURCE_TITLES[n]} ({b.name})" for n, b in engines] \
        + ([f"Telegram ({tg_backend.name})"] if tg_backend else [])
    job.say(f"План: {len(tasks)} запросов · источники: {', '.join(names)}")

    # учёт запросов по всем поисковикам, включая подменённые на ходу
    used: dict[int, object] = {}

    def track() -> None:
        for o in (places, tg_backend, *(b for _, b in engines)):
            if o is not None:
                used[id(o)] = o

    def total_calls() -> int:
        track()
        return sum(getattr(o, "calls", 0) for o in used.values())

    def calls_of(*names_: str) -> int:
        track()
        return sum(getattr(o, "calls", 0) for o in used.values() if getattr(o, "name", "") in names_)

    tg_seen: dict[str, tuple] = {}

    # ---- Apify: по одному запуску на город, параллельно с основным циклом ----
    apify_cfg = ucfg.get("apify") or {}
    prices = apify_cfg.get("price_per_1000") or {}
    city_jobs = sorted({(city, reg, cc) for _, city, reg, cc in tasks})
    apify_pool = ThreadPoolExecutor(max_workers=3) if apify_kinds else None
    apify_futs = {}
    for kind in apify_kinds:
        actor = apify_cfg.get("yandex_maps_actor" if kind == "yandex_maps" else "twogis_actor", "")
        src = ApifyMaps(keys["apify"], kind, actor)
        for city, reg, cc in city_jobs:
            def fresh(src=src, kind=kind, city=city, cc=cc):
                items = src.search(spec.keywords, city, geo.COUNTRIES[cc]["name"], spec.apify_max)
                job.apify_items[kind] = job.apify_items.get(kind, 0) + len(items)
                job.apify_cost += len(items) * float(prices.get(kind, 7)) / 1000
                return items
            key = f"apify:{kind}:{cc}:{city}:{spec.apify_max}:{'|'.join(spec.keywords)}"
            apify_futs[apify_pool.submit(cached, key, fresh)] = (src, kind, city, reg, cc)

    # ---- 1. Поиск компаний ----
    job.stage = "Поиск компаний"
    job.total, job.done = len(tasks) + len(apify_futs), 0
    found: list[Lead] = []
    by_domain: dict[str, Lead] = {}
    seen_cards: set[str] = set()
    web_count = 0
    limit_hit = False

    def add(lead: Lead) -> bool:
        if lead.website:
            dom = registered_domain(lead.website)
            if dom in by_domain:
                _absorb(by_domain[dom], lead)
                return False
            by_domain[dom] = lead
        card = lead.source_url or (lead.name + "|" + lead.address)
        if card in seen_cards:
            return False
        seen_cards.add(card)
        found.append(lead)
        return True

    for q, city, reg, cc in tasks:
        if job.cancel.is_set():
            break
        gl = geo.COUNTRIES[cc]["gl"]
        new = 0
        calls_before = total_calls()
        # кредиты кончились или упёрлись в лимит — переключаемся, а не молча теряем выдачу
        if spec.credit_limit and job.credits >= spec.credit_limit and not limit_hit:
            limit_hit = True
            drop_serper(f"Лимит {spec.credit_limit} кр. Serper выбран (потрачено {job.credits})", topup=False)
        if places is not None and places.exhausted:
            job.say("⚠ Serper: кредиты закончились — Google Карты дальше пропускаю "
                    "(лиды с телефонами из карт в этот поиск уже не попадут). Пополнить: serper.dev")
            places = None
        for i, (eng, b) in enumerate(engines):
            if getattr(b, "exhausted", False):
                nxt = free_backend(session, keys, after=b)
                job.say(f"⚠ {b.name}: запросы закончились — сайты дальше ищу через {nxt.name}.")
                engines[i] = (eng, nxt)
                if tg_backend is b:
                    tg_backend = nxt
        if tg_backend is not None and getattr(tg_backend, "exhausted", False):
            tg_backend = free_backend(session, keys, after=tg_backend)

        if places:
            items = cached(f"gmaps:{gl}:{spec.maps_pages}:{q}",
                           lambda: places.search(q, gl, spec.maps_pages)[0]) or []
            for p in items:
                lead = place_to_lead(p, cc)
                lead.query, lead.region_hint = q, city
                lead.city = detect_city(lead.address, city, city_pool) if lead.address else city
                _locate(lead, reg, cc)
                new += add(lead)
            if places.last_error:
                job.say(f"  ! Google Карты: {places.last_error}")
                places.last_error = ""

        for eng, backend in engines:
            if web_count >= spec.max_sites:
                break
            try:
                items = cached(f"web:{eng}:{backend.name}:{gl}:{spec.per_query}:{q}",
                               lambda: search_items(backend, q, spec.per_query, gl)) or []
            except Exception as e:  # noqa: BLE001
                job.say(f"  ! поиск {SOURCE_TITLES[eng]} упал [{q}]: {e}")
                items = []
            for it in items:
                link = it.get("link", "")
                if not _is_company_site(link):
                    continue
                dom = registered_domain(link)
                serp = f"{it.get('title', '')} {it.get('snippet', '')}".strip()
                if dom in by_domain:
                    if not by_domain[dom].serp_text:
                        by_domain[dom].serp_text = serp
                    continue
                if web_count >= spec.max_sites:
                    break
                lead = Lead(website=f"https://{dom}", source=eng, source_url=link, region_hint=city,
                            country=cc, region=reg, query=q, serp_text=serp)
                new += add(lead)
                web_count += 1
            err = getattr(backend, "last_error", "")
            if err:
                job.say(f"  ! {SOURCE_TITLES[eng]}: {err}")
                backend.last_error = ""

        if tg_backend is not None:
            try:
                items = cached(f"tg:{tg_backend.name}:{gl}:{spec.per_query}:{q}",
                               lambda: search_items(tg_backend, f"site:t.me {q}", spec.per_query, gl)) or []
            except Exception as e:  # noqa: BLE001
                job.say(f"  ! поиск Telegram упал [{q}]: {e}")
                items = []
            for it in items:
                u = tg_username(it.get("link", ""))
                if u and u not in tg_seen:
                    tg_seen[u] = (q, city, reg, cc, f"{it.get('title', '')} {it.get('snippet', '')}".strip())

        job.credits = calls_of("serper", "maps")          # SerperPlaces.name == "maps"
        job.yandex_calls = calls_of("yandex")
        job.done += 1
        job.say(f"[{job.done}/{len(tasks)}] «{q}» → +{new} (всего {len(found)})"
                + (f", Telegram-кандидатов {len(tg_seen)}" if tg_backend else ""))
        if total_calls() > calls_before:
            time.sleep(delay)            # пауза только если реально ходили в API (не из кеша)

    for fut in as_completed(apify_futs):
        if job.cancel.is_set():
            break
        src, kind, city, reg, cc = apify_futs[fut]
        title = MAPS_PRESETS[kind]["title"]
        try:
            items = fut.result() or []
        except Exception as e:  # noqa: BLE001
            job.say(f"  ! {title} [{city or geo.COUNTRIES[cc]['name']}]: {e}")
            items = []
        new = 0
        for it in items:
            lead = src.to_lead(it, city, cc)
            lead.query, lead.region_hint = " / ".join(spec.keywords[:3]), city
            _locate(lead, reg, cc)
            new += add(lead)
        job.done += 1
        job.say(f"{title} · {city or geo.COUNTRIES[cc]['name']}: {len(items)} организаций, +{new}")
    if apify_pool:
        apify_pool.shutdown(wait=False, cancel_futures=True)

    # ---- 1б. Telegram: открываем публичные страницы найденных каналов ----
    if tg_seen and not job.cancel.is_set():
        job.stage = "Telegram-каналы"
        job.total, job.done = len(tg_seen), 0
        max_subs = int((ucfg.get("telegram") or {}).get("max_subscribers", 50000))
        tg_sess = requests.Session()
        tg_sess.headers.update({"User-Agent": ua})
        added = skipped = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(cached, f"tgch:{u}", lambda u=u: fetch_channel(tg_sess, u)): u for u in tg_seen}
            for fut in as_completed(futs):
                if job.cancel.is_set():
                    break
                u = futs[fut]
                job.done += 1
                try:
                    ch = fut.result()
                except Exception:  # noqa: BLE001
                    ch = None
                # городские чаты, боты и крупные паблики/СМИ — не компании
                if not ch or ch["kind"] in ("group", "bot") or (ch["subscribers"] or 0) > max_subs:
                    skipped += 1
                    continue
                q, city, reg, cc, serp = tg_seen[u]
                lead = channel_to_lead(ch, cc)
                lead.query, lead.region_hint, lead.serp_text = q, city, serp
                lead.city = detect_city(" ".join([ch["description"], *ch["posts"]]), city, city_pool)
                _locate(lead, reg, cc)
                added += add(lead)
        job.say(f"Telegram: проверено {len(tg_seen)}, добавлено {added}, "
                f"отсеяно (чаты, боты, крупные паблики) {skipped}")

    job.say(f"Найдено кандидатов: {len(found)} (сайтов из выдачи: {web_count})"
            + (f" · из кеша: {job.cache_hits}" if job.cache_hits else ""))

    # ---- 2. Обход сайтов ----
    to_crawl = [l for l in found if l.website] if spec.crawl and not job.cancel.is_set() else []
    crawl_ids = {id(l) for l in to_crawl}
    leads: list[Lead] = []
    ai_text_limit = int((ucfg.get("ai") or {}).get("max_chars", 6000))

    def finish(lead: Lead) -> None:
        _score(lead, profile, ai_text_limit)
        leads.append(lead)
        job.publish(lead)

    for l in found:
        if id(l) not in crawl_ids:
            if spec.check_email and l.emails:
                l.emails, _ = filter_emails(l.emails)
            finish(l)

    if to_crawl:
        job.stage = "Сбор контактов с сайтов"
        job.total, job.done = len(to_crawl), 0
        bad_emails = 0

        def crawl(lead: Lead) -> int:
            enrich_one(lead, int(we.get("timeout", 12)), int(we.get("max_pages_per_site", 5)), ua,
                       True, city_pool, job.renderer, proxies)
            if spec.check_email and lead.emails:
                lead.emails, bad = filter_emails(lead.emails)
                return len(bad)
            return 0

        renders_before = job.renderer.count if job.renderer else 0
        pool = ThreadPoolExecutor(max_workers=int(we.get("max_workers", 16)))
        futs = {pool.submit(crawl, l): l for l in to_crawl}
        processed: set[int] = set()
        try:
            for fut in as_completed(futs):
                if job.cancel.is_set():
                    break
                lead = futs[fut]
                processed.add(id(lead))
                try:
                    bad_emails += fut.result()
                except Exception:  # noqa: BLE001 — упавший сайт просто без контактов
                    pass
                job.done += 1
                if job.done % 25 == 0:
                    job.say(f"  обработано сайтов: {job.done}/{job.total}")
                if lead.source in ("search", "yandex") and not (lead.site_text or lead.phones or lead.emails):
                    continue  # сайт не открылся и контактов нет — мёртвый лид
                finish(lead)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for l in to_crawl:
            if id(l) not in processed:
                finish(l)   # остановили на полпути: оцениваем по выдаче/карточке
        if bad_emails:
            job.say(f"Отброшено почт с несуществующим доменом: {bad_emails}")
        rendered = (job.renderer.count - renders_before) if job.renderer else 0
        if rendered:
            job.say(f"JS-сайтов дорендерено через Chromium: {rendered}")

    # ---- 3. ИИ-проверка по смыслу ----
    _ai_stage(job, leads, keys, ucfg)

    # ---- 4. Юр.данные DaData (РФ) ----
    _legal_stage(job, leads, keys)

    for l in leads:
        l.site_text = ""   # текст сайтов больше не нужен — не держим мегабайты в памяти

    # ---- 5. Склейка, запись в базу, выгрузка ----
    job.stage = "Сохранение в базу"
    rejected = [l for l in leads if l.reject_reason]
    kept = merge_leads([l for l in leads if not l.reject_reason])
    new = 0
    for l in kept:
        cid, is_new = db.upsert(lead_dict(l), job.id)
        db.add_run_lead(job.id, cid, l.score, l.query, l.matched_keywords, is_new, l.region_hint)
    rows = db.run_rows(job.id)
    new = sum(1 for r in rows if r.get("is_new"))
    job.new_count = new
    job.replace_rows(rows, final=True)
    if rejected:
        reasons: dict[str, int] = {}
        for l in rejected:
            reasons[l.reject_reason.split(":")[0]] = reasons.get(l.reject_reason.split(":")[0], 0) + 1
        job.say("Отсеяно: " + ", ".join(f"{k} — {v}" for k, v in reasons.items()))
    target = sum(1 for r in rows if (r.get("score") or 0) >= spec.threshold)
    job.say(f"Итого компаний: {len(rows)}, целевых (≥ {spec.threshold:.2f}): {target}, "
            f"впервые найдено: {new}")
    _save_xlsx(job, rows)
    if spec.notify or job.schedule_id:
        _notify(job, rows, keys)


def _ai_stage(job: Job, leads: list[Lead], keys: dict, ucfg: dict) -> None:
    spec = job.spec
    if not spec.ai or job.cancel.is_set():
        return
    if not keys.get("anthropic"):
        job.say("⚠ ИИ-проверка: нет ANTHROPIC_API_KEY — пропущено (оценка только по словам).")
        return
    from .ai import AI, NOT_A_LEAD_TYPES

    acfg = ucfg.get("ai") or {}
    ai = AI(keys["anthropic"], acfg)
    min_score = float(acfg.get("min_score", 0.3))
    cands = sorted((l for l in leads if not l.reject_reason and l.score >= min_score),
                   key=lambda l: l.score, reverse=True)[: spec.ai_max]
    if not cands:
        return
    job.stage = f"ИИ-проверка ({ai.model})"
    job.total, job.done = len(cands), 0
    wanted = spec.context or "; ".join(spec.keywords)
    if spec.sphere:
        wanted = f"{wanted} (сфера: {spec.sphere})"

    def check(lead: Lead):
        return ai.classify(wanted, spec.keywords, {
            "name": lead.name, "website": lead.website, "rubrics": lead.rubrics,
            "description": lead.description, "serp_text": lead.serp_text, "address": lead.address,
            "site_text": lead.site_text})

    pool = ThreadPoolExecutor(max_workers=int(acfg.get("workers", 6)))
    futs = {pool.submit(check, l): l for l in cands}
    no = 0
    try:
        for fut in as_completed(futs):
            if job.cancel.is_set() or ai.disabled:
                break
            lead = futs[fut]
            job.done += 1
            try:
                v = fut.result()
            except Exception:  # noqa: BLE001
                v = None
            job.ai_cost = ai.cost
            if not v:
                continue
            lead.ai_verdict = "yes" if v.relevant else "no"
            lead.ai_type, lead.ai_reason = v.company_type, v.reason
            lead.ai_confidence, lead.ai_services = v.confidence, v.services
            if not v.relevant or v.company_type in NOT_A_LEAD_TYPES:
                lead.score = round(min(lead.score, 0.2), 2)
                no += 1
            else:
                lead.score = round(min(1.0, 0.35 * lead.score + 0.65 * v.confidence / 100), 2)
            if job.done % 20 == 0:
                job.say(f"  ИИ проверил: {job.done}/{job.total} · ${ai.cost:.2f}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if ai.disabled:
        job.say(f"⚠ ИИ-проверка остановлена: {ai.disabled}")
    job.ai_cost = ai.cost
    job.say(f"ИИ проверил {ai.calls} компаний, отклонил {no} · потрачено ${ai.cost:.2f}"
            + (f" · ошибок: {ai.errors} ({ai.last_error[:120]})" if ai.errors else ""))
    job.replace_rows([lead_json(l) for l in sorted(leads, key=lambda l: l.score, reverse=True)])


def _legal_stage(job: Job, leads: list[Lead], keys: dict) -> None:
    spec = job.spec
    if not spec.legal or job.cancel.is_set() or not keys.get("dadata"):
        return
    from .sources.dadata import DEAD_STATUSES, enrich_one as dadata_one

    cands = [l for l in leads if l.country == "RU" and not l.reject_reason
             and l.score >= spec.threshold - 0.1 and (l.inn or l.name)]
    if not cands:
        return
    job.stage = "Юр. данные (DaData)"
    job.total, job.done = len(cands), 0
    sess = requests.Session()
    dead = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        for fut in as_completed({pool.submit(dadata_one, l, sess, keys["dadata"]): l for l in cands}):
            job.done += 1
            try:
                fut.result()
            except Exception:  # noqa: BLE001
                pass
    for l in cands:
        if l.legal_status in DEAD_STATUSES:
            l.reject_reason = f"юрлицо: {l.legal_status}"
            dead += 1
    found = sum(1 for l in cands if l.legal_name)
    job.say(f"DaData: реквизиты найдены у {found} из {len(cands)}" + (f", ликвидированных отсеяно: {dead}" if dead else ""))


def _locate(lead: Lead, reg: str, cc: str) -> None:
    """Регион/страна по городу из справочника, иначе — из задачи поиска."""
    lcc, lreg = geo.locate(lead.city) if lead.city else ("", "")
    lead.country = lcc or lead.country or cc
    lead.region = lreg or lead.region or reg


def _absorb(base: Lead, other: Lead) -> None:
    for p in other.phones:
        if p not in base.phones:
            base.phones.append(p)
    for e in other.emails:
        if e not in base.emails:
            base.emails.append(e)
    base.socials = merge_socials(base.socials, other.socials)
    for f in ("address", "rating", "name", "inn"):
        if not getattr(base, f) and getattr(other, f):
            setattr(base, f, getattr(other, f))
    base.rubrics += [r for r in other.rubrics if r not in base.rubrics]
    if other.source not in base.source.split("+"):
        base.source += "+" + other.source


def _score(lead: Lead, profile: Profile, keep_chars: int) -> None:
    if not lead.name:
        lead.name = registered_domain(lead.website) if lead.website else "—"
    if lead.city:
        lcc, lreg = geo.locate(lead.city)
        if lreg:
            lead.country, lead.region = lcc, lreg
    # телефоны честнее текста: у сайта с одними белорусскими номерами страна не RU
    by = sum(p.startswith("+375") for p in lead.phones)
    ru = sum(p.startswith("+7") for p in lead.phones)
    if by and by >= ru:
        if lead.country != "BY":
            lead.country, lead.region, lead.city = "BY", "", ""
    elif not lead.country and ru:
        lead.country = "RU"
    head = " ".join([lead.name, *lead.rubrics, lead.serp_text, lead.description])
    lead.score, lead.matched_keywords, lead.reject_reason = profile.score(
        head, lead.site_text, from_maps=lead.source.split("+")[0] in ("maps", "yandex_maps", "2gis"))
    if lead.source == "telegram" and not lead.reject_reason \
            and not profile.head_hit(" ".join([lead.name, lead.description, *lead.rubrics])):
        # выдача site:t.me — посты пабликов, блоги, а у DuckDuckGo и откровенный спам/18+.
        # Без ключевых слов в названии или описании канал не профильный — не сохраняем вовсе
        lead.score, lead.reject_reason = 0.0, "Telegram: канал не по теме"
    lead.site_text = lead.site_text[:keep_chars]   # для ИИ хватит начала, остальное освобождаем


def _save_xlsx(job: Job, rows: list[dict]) -> None:
    out_dir = Path(job.cfg.get("output", {}).get("dir", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in job.title[:40]).strip().replace(" ", "_")
    xlsx = out_dir / f"leads_{safe}_{stamp}.xlsx"
    rows_to_xlsx(rows, xlsx, job.spec.threshold)
    job.files = [str(xlsx)]
    job.say(f"Сохранено: {xlsx}")


def _notify(job: Job, rows: list[dict], keys: dict) -> None:
    from .notify import run_summary, send_telegram

    if not keys.get("telegram_token"):
        return
    new_rows = [r for r in rows if r.get("is_new") and (r.get("score") or 0) >= job.spec.threshold]
    err = send_telegram(keys, run_summary(job.title, new_rows, len(rows), keys.get("public_url", "")))
    job.say("Telegram: уведомление отправлено" if not err else f"⚠ Telegram: {err}")
