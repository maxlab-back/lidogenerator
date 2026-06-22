"""Оркестратор: поиск сайтов -> краулинг и извлечение -> классификация -> экспорт."""
from __future__ import annotations

import requests

from .classify import classify
from .dedupe import dedupe
from .export import export
from .models import Lead
from .sources.search import build_backend, discover
from .sources.website import enrich_leads


def run(cfg: dict) -> dict:
    net = cfg.get("network", {})
    user_agent = net.get("user_agent", "Mozilla/5.0 (compatible; LeadFinder/1.0)")
    keys = cfg.get("_keys", {})

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    sources_cfg = cfg.get("sources", {})
    search_cfg = sources_cfg.get("search", {})
    queries = cfg.get("queries", [])
    regions = cfg.get("regions", [])

    leads: list[Lead] = []

    # 1а) Поиск сайтов (SERP) -> кандидаты-домены
    if search_cfg.get("enabled", True):
        backend = build_backend(search_cfg, keys, session)
        print(f"[Поиск] бэкенд: {backend.name}, регионов: {len(regions) or 1}, "
              f"запросов: {len(queries)}")
        candidates = discover(backend, queries, regions, search_cfg, user_agent)
        print(f"[Поиск] уникальных доменов: {len(candidates)}")
        leads += [Lead(website=url, source="search", source_url=url, region_hint=hint)
                  for url, hint in candidates]

    # 1б) 2ГИС через Apify -> организации с контактами
    apify_cfg = sources_cfg.get("apify", {})
    if apify_cfg.get("enabled", False):
        if not keys.get("apify"):
            print("⚠️  apify.enabled=true, но нет APIFY_TOKEN — пропущено.")
        else:
            from .sources.apify import ApifySource
            src = ApifySource(
                keys["apify"], apify_cfg.get("actor", "m_mamaev/2gis-places-scraper"),
                queries, apify_cfg.get("max_items_per_city", 120),
                language=apify_cfg.get("language", "ru"),
                domain=apify_cfg.get("domain", "2gis.ru"),
            )
            cities = regions if regions else ["Москва"]
            max_cities = apify_cfg.get("max_cities", 0)
            if max_cities:
                cities = cities[:max_cities]   # Apify платный — ограничиваем число городов
            for i, city in enumerate(cities, 1):
                try:
                    found = src.search_city(city)
                except Exception as e:
                    print(f"   ! Apify [{city}]: {e}")
                    found = []
                print(f"[Apify 2ГИС {i}/{len(cities)}] {city} -> {len(found)}")
                leads += found

    if not leads:
        raise SystemExit("Нет кандидатов ни из одного источника. Смотри предупреждения выше.")

    # 2) Краулинг сайтов: контакты, имя, город, подтверждение ниши
    print("Захожу на сайты и извлекаю данные...")
    we = cfg.get("sources", {}).get("website_enrich", {})
    enrich_leads(leads, we, user_agent)

    # отсекаем мёртвые сайты (без имени и без контактов)
    leads = [l for l in leads if l.name or l.phones or l.emails]

    # 3) Классификация
    lead_types = cfg.get("lead_types", {})
    for l in leads:
        classify(l, lead_types)

    # 4) Дедуп (теперь есть телефоны — добиваем дубли между доменами)
    leads = dedupe(leads)
    print(f"Лидов после краулинга и дедупа: {len(leads)}")

    # 4.5) Обогащение юр.данными через DaData (ИНН/ОГРН/ОКВЭД/руководитель)
    dd = cfg.get("sources", {}).get("dadata", {})
    if dd.get("enabled", False):
        if not keys.get("dadata"):
            print("⚠️  dadata.enabled=true, но нет DADATA_API_KEY в .env — пропущено.")
        else:
            print("Обогащаю юр.данными через DaData...")
            from .sources.dadata import enrich_with_dadata
            # только лиды, прошедшие порог (экономим лимит запросов)
            threshold = cfg.get("score_threshold", 0.45)
            to_enrich = [l for l in leads if l.lead_type and l.score >= threshold]
            enrich_with_dadata(to_enrich, keys["dadata"], dd.get("max_workers", 5))

    # 5) Экспорт
    result = export(leads, cfg)
    print("\nГотово. Файлы:")
    for f in result["files"]:
        print(f"  {f}")
    print("\nИтог по типам:")
    for name, cnt in result["counts"].items():
        print(f"  {name}: {cnt}")
    return result
