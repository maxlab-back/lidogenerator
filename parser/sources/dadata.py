"""Обогащение лидов юр.данными из DaData (ЕГРЮЛ): ИНН, ОГРН, ОКВЭД, руководитель, юр.адрес.

Бесплатно 10 000 запросов/день. Ключ: https://dadata.ru/ -> Профиль -> API-ключи.
Документация: https://dadata.ru/api/suggest/party/
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SUGGEST_PARTY = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower().replace("ё", "е") if ch.isalnum())


# правовые формы и общие/отраслевые слова, которые НЕ являются названием фирмы:
# по ним матчить нельзя (иначе "Интернет магазин окон" -> любое ООО с этими словами)
STOP_WORDS = {
    "ооо", "оао", "зао", "пао", "ип", "ао", "нпо", "нпф", "тд", "пк", "групп", "group",
    "торговый", "торговая", "дом", "компания", "фирма", "холдинг", "корпорация", "центр",
    "интернет", "магазин", "онлайн", "online", "shop", "store", "сайт", "маркет",
    "окна", "окно", "оконный", "оконные", "подоконник", "подоконники", "сэндвич", "сендвич",
    "панель", "панели", "откос", "откосы", "пвх", "пластик", "пластиковые", "пластиковый",
    "завод", "фабрика", "производство", "производитель", "производственная", "произв",
    "строительный", "строй", "мир", "опт", "розница", "купить", "цена", "заказ", "размер",
    "недорого", "дешево", "официальный", "продажа", "доставка", "ремонт", "услуги",
    "окон", "оконных", "двери", "дверей",
}

# доменные хвосты, которые надо срезать с токена бренда (ВсеПодоконники.ру -> всеподоконники)
_TLDS = ("ру", "рф", "com", "net", "ru", "org", "biz", "pro", "shop", "online")


def _tokens(s: str) -> set[str]:
    # значимые слова (>=3 символов), без правовых форм и общих/отраслевых слов
    out = set()
    for w in s.lower().replace("ё", "е").replace('"', " ").split():
        w = "".join(ch for ch in w if ch.isalnum())
        if len(w) >= 3 and w not in STOP_WORDS:
            out.add(w)
    return out


def _strip_tld(w: str) -> str:
    for tld in _TLDS:
        if len(w) > len(tld) + 3 and w.endswith(tld):
            return w[: -len(tld)]
    return w


def _name_matches(lead_name: str, sug_value: str, data: dict) -> bool:
    """Похоже ли найденное юрлицо на имя лида (защита от чужих реквизитов).

    Матчим только если в названии лида есть РАЗЛИЧИТЕЛЬНОЕ слово-бренд (не из STOP_WORDS),
    которое встречается в названии юрлица. Имена из одних общих слов ("Интернет магазин окон",
    "Строительный мир") по названию не подтверждаем — для них нужен ИНН из футера сайта.
    """
    cand_raw = sug_value + " " + (data.get("name") or {}).get("short_with_opf", "")
    cand_norm = _norm(cand_raw)
    for d in _tokens(lead_name):
        d = _strip_tld(d)
        if len(d) >= 4 and d in cand_norm:
            return True
    return False


def lookup(session: requests.Session, api_key: str, query: str) -> dict | None:
    """Вернуть data лучшей организации по названию или ИНН (или None)."""
    try:
        r = session.post(
            SUGGEST_PARTY,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"query": query, "count": 5},
            timeout=15,
        )
        r.raise_for_status()
        suggestions = r.json().get("suggestions") or []
    except (requests.RequestException, ValueError):
        return None
    if not suggestions:
        return None

    # предпочитаем действующие организации
    active = [s for s in suggestions if (s.get("data") or {}).get("state", {}).get("status") == "ACTIVE"]
    pool = active or suggestions
    return pool[0]


def _okved(data: dict) -> str:
    """ОКВЭД в виде 'код название', если название доступно, иначе только код."""
    code = data.get("okved", "") or ""
    if not code:
        return ""
    for o in (data.get("okveds") or []):
        if o.get("code") == code and o.get("name"):
            return f"{code} {o['name']}".strip()
    return code


def enrich_one(lead, session: requests.Session, api_key: str) -> None:
    # ищем по ИНН (точно), если уже есть, иначе по названию
    by_inn = bool(lead.inn)
    query = lead.inn or lead.name
    if not query:
        return
    sug = lookup(session, api_key, query)
    if not sug:
        return
    data = sug.get("data") or {}

    # защита от ложного матча: при поиске по названию требуем реальное совпадение,
    # иначе НЕ приклеиваем чужие ИНН/ОГРН/руководителя.
    if not by_inn and not _name_matches(lead.name, sug.get("value", ""), data):
        return

    lead.inn = data.get("inn", "") or lead.inn
    lead.ogrn = data.get("ogrn", "") or lead.ogrn
    lead.legal_name = (data.get("name") or {}).get("full_with_opf", "") or sug.get("value", "")
    lead.okved = _okved(data)
    mgmt = data.get("management") or {}
    lead.manager = mgmt.get("name", "") or ""
    lead.legal_address = (data.get("address") or {}).get("value", "") or ""
    lead.dadata_matched = True


def enrich_with_dadata(leads: list, api_key: str, max_workers: int = 5) -> None:
    session = requests.Session()
    targets = [l for l in leads if (l.name or l.inn)]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_one, l, session, api_key): l for l in targets}
        done = 0
        errors = 0
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                errors += 1
                if errors <= 10:
                    print(f"    ! DaData {futures[f].name}: {e}")
            done += 1
            if done % 50 == 0:
                print(f"    DaData: {done}/{len(targets)}")
