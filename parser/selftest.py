"""Оффлайн-проверка классификатора, телефонов, релевантности и учёта кредитов (без сети)."""
from __future__ import annotations

from .classify import classify
from .models import Lead

# (текст карточки, подтверждено_сайтом, ожидаемый_тип)
CASES = [
    ("Подоконники ПВХ нарезка в размер", True, "podokonnik_pvh"),
    ("Подоконники пластиковые на заказ, распил", False, "podokonnik_pvh"),
    ("Сэндвич-панели для откосов в размер", True, "sandwich_otkos"),
    ("Сендвич панель откосы нарезка под заказ", False, "sandwich_otkos"),
    ("Деревянные подоконники из массива дуба", False, ""),          # block -> не подоконник_пвх
    ("Кровельные сэндвич-панели для ангаров", False, ""),           # block -> не sandwich
    ("Кафе Ромашка, кофе и десерты", False, ""),                    # мимо
    ("Окна пластиковые, подоконники ПВХ, откосы сэндвич в размер", True, None),  # любой из двух
]


# (сырой телефон, страна-подсказка, ожидаемый каноничный вид)
PHONE_CASES = [
    ("+7 (999) 123-45-67", "", "+79991234567"),
    ("8 999 123 45 67", "", "+79991234567"),
    ("8 800 555-35-35", "", "+78005553535"),
    ("8 (029) 237-00-50", "", "+375292370050"),     # РБ внутренний формат — не +7!
    ("+375 29 885-55-11", "", "+375298855511"),
    ("8 0152 71-57-77", "", "+375152715777"),
    ("291234567", "BY", "+375291234567"),
    ("291234567", "", ""),
]

# (ключевые слова, контекст, минус, шапка, текст сайта, из карт, условие)
RELEVANCE_CASES = [
    (["ремонт квартир под ключ"], "своя бригада, отделка, смета", ["вакансии"],
     "Ремонт квартир под ключ в Минске — СтройДом", "выполняем отделку, смета бесплатно, своя бригада",
     False, lambda s: s >= 0.8),
    (["ремонт квартир под ключ"], "", ["вакансии"],
     "Вакансии: мастер по ремонту квартир", "", False, lambda s: s == 0),
    (["ремонт квартир под ключ"], "бригада отделка", [],
     "Кафе Ромашка", "меню кофе десерты", False, lambda s: s < 0.3),
    (["автосервис"], "ремонт диагностика", [],
     "СТО АвтоМарКа Ремонт и обслуживание автомобилей", "", True, lambda s: s >= 0.5),
    (["подоконник ПВХ в размер"], "", ["деревянные подоконники"],
     "Деревянные подоконники из массива", "", False, lambda s: s == 0),
]


def run_universal_self_test() -> None:
    from .relevance import Profile
    from .sources.website import BY_PHONE_RE, normalize_phone

    ok = total = 0
    print("\nСамопроверка телефонов:\n")
    for raw, cc, want in PHONE_CASES:
        got = normalize_phone(raw, cc)
        passed = got == want
        ok += passed; total += 1
        print(f"  [{'OK ' if passed else 'FAIL'}] {raw!r:24} ({cc or '—'}) -> {got or '—'}")
    text = "Звоните: 8 (029) 237-00-50, +375 17 222-33-44, цена 8 000 руб"
    found = [normalize_phone(x) for x in BY_PHONE_RE.findall(text)]
    passed = found == ["+375292370050", "+375172223344"]
    ok += passed; total += 1
    print(f"  [{'OK ' if passed else 'FAIL'}] извлечение РБ-номеров из текста -> {found}")

    print("\nСамопроверка релевантности:\n")
    for kws, ctx, minus, head, body, maps, cond in RELEVANCE_CASES:
        score, matched, reason = Profile(kws, ctx, minus).score(head, body, from_maps=maps)
        passed = cond(score)
        ok += passed; total += 1
        print(f"  [{'OK ' if passed else 'FAIL'}] «{head[:44]:44}» -> {score} {reason or matched}")

    print("\nСамопроверка определения города:\n")
    for name, passed, got in _city_checks():
        ok += passed; total += 1
        print(f"  [{'OK ' if passed else 'FAIL'}] {name:56} -> {got}")

    print("\nСамопроверка кредитов и запасного поиска:\n")
    for name, passed, got in _credit_checks():
        ok += passed; total += 1
        print(f"  [{'OK ' if passed else 'FAIL'}] {name:52} -> {got}")
    print(f"\nИтог: {ok}/{total} прошло.")


def _city_checks() -> list[tuple[str, bool, object]]:
    """Падежи и адрес сайта: из-за них федеральные сайты получали город «Москва»."""
    from . import geo
    from .cities import city_from_url, detect_city

    pool = geo.all_cities(["RU"])
    site = "Купить фасады и сайдинг в Переславле-Залесском с доставкой по России. Офис в Москве."
    cases = [
        ("город из поддомена", city_from_url("https://pereslavl-zalesskii.saiding77.ru", pool), "Переславль-Залесский"),
        ("город из домена", city_from_url("https://yaroslavl-okna.ru", pool), "Ярославль"),
        ("короткое название целым словом", city_from_url("https://www.tula-stroy.ru", pool), "Тула"),
        ("чужой домен города не даёт", city_from_url("https://grandline.ru", pool), ""),
        ("«Ростов» не выдаём за Ростов Великий", city_from_url("https://rostov.okna.ru", pool), ""),
        ("город запроса в падеже находится", detect_city(site, "Переславль-Залесский", pool), "Переславль-Залесский"),
        ("без подсказки берём частый город", detect_city("Москва, Москва, Ярославль", "", pool), "Москва"),
        ("адрес сайта важнее текста", detect_city(site, "", pool, url="https://pereslavl-zalesskii.saiding77.ru"),
         "Переславль-Залесский"),
    ]
    return [(name, got == want, f"{got or '—'} (ждали {want or '—'})") for name, got, want in cases]


def _credit_checks() -> list[tuple[str, bool, object]]:
    """Смета Serper, потолок трат, лестница бесплатных движков и разбор выдачи DuckDuckGo."""
    import requests

    from .sources.search import DuckDuckGoBackend, GoogleCSEBackend, free_backend
    from .universal import Spec, estimate, serper_credits

    caps = {"backend": "serper", "serper": True}
    base = {"keywords": ["а", "б"], "custom_cities": ["Калуга", "Тула", "Обнинск"]}
    spec = Spec.from_json({**base, "per_query": 20, "maps_pages": 1})
    # 6 запросов × (2 страницы выдачи + 1 страница карт + 2 страницы Telegram)
    credits = serper_credits(spec, 6, web=True, maps=True, telegram=True)
    est_short = estimate(spec, caps, {}, balance=6)
    est_limit = estimate(Spec.from_json({**base, "credit_limit": 5}), caps, {}, balance=1000)
    sess = requests.Session()
    cse_keys = {"google_cse_key": "k", "google_cse_cx": "c"}
    html = ('<div class="result results_links">'
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpotolki40.ru%2F">Потолки в Калуге</a>'
            '<a class="result__snippet">Монтаж натяжных потолков за день</a></div>')
    items = DuckDuckGoBackend._parse_results(html)
    first = items[0] if items else {}

    return [
        ("смета Serper: 6 запросов × (2+1+2) страниц", credits == 30, credits),
        ("баланс меньше сметы -> enough=False", est_short.get("enough") is False, est_short.get("enough")),
        ("баланс меньше сметы -> хватит на часть запросов",
         0 <= est_short.get("covered_tasks", -1) < est_short["tasks"], est_short.get("covered_tasks")),
        ("потолок трат режет смету", est_limit["credits"] == 5, est_limit["credits"]),
        # 6 запросов по 3 кредита (2 страницы выдачи + 1 карт): потолка в 5 хватает на один
        ("потолок трат: сколько запросов пройдёт платно",
         est_limit.get("limited_tasks") == 1, est_limit.get("limited_tasks")),
        ("бесплатная ступень без ключей -> DuckDuckGo",
         free_backend(sess, {}).name == "duckduckgo", free_backend(sess, {}).name),
        ("бесплатная ступень с ключами CSE -> Google CSE",
         free_backend(sess, cse_keys).name == "google_cse", free_backend(sess, cse_keys).name),
        ("после исчерпания CSE -> DuckDuckGo",
         free_backend(sess, cse_keys, after=GoogleCSEBackend(sess, "k", "c")).name == "duckduckgo",
         free_backend(sess, cse_keys, after=GoogleCSEBackend(sess, "k", "c")).name),
        ("разбор выдачи DuckDuckGo: ссылка", first.get("link") == "https://potolki40.ru/", first.get("link")),
        ("разбор выдачи DuckDuckGo: заголовок и сниппет",
         bool(first.get("title")) and "Монтаж" in (first.get("snippet") or ""),
         f"{first.get('title', '')} / {first.get('snippet', '')}"),
    ]


def run_self_test(cfg: dict) -> None:
    lead_types = cfg.get("lead_types", {})
    ok = 0
    print("Самопроверка классификатора:\n")
    for text, confirmed, expected in CASES:
        l = Lead(name=text, raw_text=text, site_confirmed=confirmed)
        classify(l, lead_types)
        got = l.lead_type or ""
        status = "?"
        if expected is None:
            passed = got != ""           # должен попасть хоть в какой-то тип
        else:
            passed = got == expected
        status = "OK " if passed else "FAIL"
        ok += int(passed)
        print(f"  [{status}] «{text[:48]:48}» -> {got or '—':16} "
              f"score={l.score} kw={l.matched_keywords}")
    print(f"\nИтог: {ok}/{len(CASES)} прошло.")
