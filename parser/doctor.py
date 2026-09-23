"""Диагностика: что установлено, какие ключи есть и что из-за их отсутствия не работает.

Вызывается из `python app.py --check` и из `./start.sh --check`. Ничего не меняет и не тратит
кредиты: единственный сетевой запрос — бесплатная проверка остатка Serper (её можно выключить).
"""
from __future__ import annotations

import socket
import sys
from importlib import metadata
from pathlib import Path

OK, WARN, BAD = "✓", "!", "✗"

# ключ в .env -> (что включает, что будет без него)
KEY_INFO = [
    (("serper",), "SERPER_API_KEY",
     "Google Карты и быстрый поиск сайтов",
     "карт не будет совсем, сайты — через бесплатный DuckDuckGo (меньше и медленнее)"),
    (("anthropic",), "ANTHROPIC_API_KEY",
     "ИИ-проверка компаний по смыслу и подбор запросов",
     "каталоги и агрегаторы отсекаются только по словам — мусора в выдаче заметно больше"),
    (("apify",), "APIFY_TOKEN",
     "Яндекс Карты (и 2ГИС на платном плане Apify)",
     "останутся только Google Карты, а без Serper — вообще никаких карт"),
    (("dadata",), "DADATA_API_KEY",
     "юр. данные РФ: ИНН, статус, штат, выручка",
     "ликвидированные и банкроты не отсеиваются"),
    (("yandex_search_key", "yandex_folder"), "YANDEX_SEARCH_API_KEY + YANDEX_FOLDER_ID",
     "выдача Яндекса (по РФ она полнее Google для местного бизнеса)",
     "источник «Сайты из Яндекса» недоступен"),
    (("telegram_token", "telegram_chat"), "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID",
     "уведомления автопоиска о новых компаниях",
     "автопоиск работает, но о новых лидах придётся узнавать самому"),
    (("bitrix24_webhook",), "BITRIX24_WEBHOOK", "выгрузка лидов в Битрикс24", "кнопка «В CRM» без Битрикса"),
    (("amocrm_host", "amocrm_token"), "AMOCRM_HOST + AMOCRM_TOKEN", "выгрузка лидов в amoCRM",
     "кнопка «В CRM» без amoCRM"),
]

PACKAGES = ["requests", "PyYAML", "beautifulsoup4", "openpyxl", "python-dotenv", "anthropic",
            "pydantic", "dnspython", "lxml"]


def _line(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def report(cfg: dict, port: int = 8765, network: bool = True) -> int:
    """Печатает отчёт. -> код возврата: 0 — можно работать, 1 — нечем искать."""
    keys = cfg.get("_keys", {})
    ucfg = cfg.get("universal") or {}

    print("Python и пакеты")
    _line(OK, f"Python {sys.version.split()[0]} — {sys.executable}")
    missing = []
    installed = []
    for name in PACKAGES:
        try:
            installed.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    _line(OK, ", ".join(installed) or "ничего не найдено")
    from .htmlutil import PARSER
    if missing == ["lxml"]:
        _line(WARN, "нет lxml — HTML разбирает html.parser из стандартной библиотеки (это допустимо)")
    elif missing:
        _line(BAD, f"не установлены: {', '.join(missing)} — выполни ./start.sh --setup")
    _line(OK, f"разбор HTML: {PARSER}")

    print("\nКлючи (.env)")
    have = {}
    for names, title, gives, without in KEY_INFO:
        got = all(keys.get(n) for n in names)
        have[names[0]] = got
        if got:
            _line(OK, f"{title} — {gives}")
        else:
            _line(WARN, f"{title} — нет. Без него: {without}")

    print("\nИсточники поиска")
    balance = None
    if have.get("serper") and network:
        from .sources.search import serper_balance
        balance = serper_balance(keys["serper"])
    if have.get("serper"):
        if balance is None:
            _line(WARN, "Serper: ключ есть, остаток узнать не удалось (сеть?)")
        elif balance == 0:
            _line(BAD, "Serper: кредиты кончились. Google Карты отключатся, сайты уйдут на DuckDuckGo. "
                       "Пополнить: serper.dev")
        elif balance < 200:
            _line(WARN, f"Serper: осталось {balance} кредитов — это на 1–2 небольших поиска. "
                        f"Пополнить: serper.dev")
        else:
            _line(OK, f"Serper: {balance} кредитов")
    else:
        _line(WARN, "Serper: ключа нет — сайты ищутся через DuckDuckGo, карт нет")
    free_web = "Google CSE (100 запросов в день бесплатно)" if all(
        keys.get(k) for k in ("google_cse_key", "google_cse_cx")) else "DuckDuckGo"
    _line(OK, f"бесплатный запасной поиск: {free_web}")

    print("\nОстальное")
    from .render import find_chrome
    chrome = find_chrome((ucfg.get("render") or {}).get("chrome_path", ""))
    _line(OK, f"Chromium: {chrome}") if chrome else \
        _line(WARN, "Chromium не найден — сайты на JavaScript отдадут меньше контактов")

    from .config import ROOT
    p = Path(ucfg.get("db") or "output/leads.db")
    db_path = p if p.is_absolute() else ROOT / p
    if db_path.exists():
        from .db import DB
        db = DB(db_path)
        st = db.stats()
        runs = len(db.runs(limit=1000))
        sched = [s for s in db.schedules() if s["enabled"]]
        users = db.users()
        _line(OK, f"База: {db_path} — компаний {st['total']}, поисков {runs}"
                  + (f", активных автопоисков {len(sched)}" if sched else ""))
        _line(OK, f"Пользователи: {', '.join(users)}" if users
              else "Пользователей нет — вход не требуется (работает только с этой машины)")
    else:
        _line(OK, f"База будет создана при первом поиске: {db_path}")
    _line(OK, f"Порт {port} свободен") if _port_free(port) else \
        _line(WARN, f"Порт {port} занят — запусти с другим: ./start.sh --port 8766")

    broken = [m for m in missing if m != "lxml"]     # lxml необязателен, остальное — обязательно
    print()
    if broken:
        print(f"Итог: не хватает пакетов ({', '.join(broken)}) — выполни ./start.sh --setup")
    elif not have.get("serper"):
        print("Итог: работать можно, но на бесплатных источниках — качество и объём заметно ниже.")
        print("Ключи вписываются прямо в интерфейсе: вкладка «Ключи».")
    elif not have.get("anthropic"):
        print("Итог: всё готово к поиску. ИИ-проверка выключена — в выдаче будет больше каталогов.")
        print("Ключ Claude вписывается прямо в интерфейсе: вкладка «Ключи».")
    else:
        print("Итог: всё на месте.")
    return 1 if broken else 0


def recheck_cities(cfg: dict, apply: bool = False) -> dict:
    """Пересчитать город и страну у компаний в базе по новым правилам.

    Город берём из адреса сайта (`yaroslavl.saiding-market.ru`) — но только у тех,
    у кого нет адреса из карт: карточка с адресом точнее поддомена. Страну правим по
    телефонам: у сайта с белорусскими номерами не может быть страны RU.
    """
    import json

    from . import geo
    from .cities import city_from_url
    from .config import ROOT
    from .db import DB

    p = Path((cfg.get("universal") or {}).get("db") or "output/leads.db")
    db = DB(p if p.is_absolute() else ROOT / p)
    pool = geo.all_cities(["RU", "BY"])
    changed = []
    with db.lock:
        rows = db.conn.execute("SELECT id, name, city, country, region, address, website, phones "
                               "FROM companies").fetchall()
    for r in rows:
        fields = {}
        if not r["address"]:
            city = city_from_url(r["website"] or "", pool)
            if city and city != r["city"]:
                cc, reg = geo.locate(city)
                fields.update(city=city, country=cc or r["country"], region=reg or r["region"])
        phones = json.loads(r["phones"] or "[]")
        by = sum(x.startswith("+375") for x in phones)
        ru = sum(x.startswith("+7") for x in phones)
        if by and by >= ru and r["country"] != "BY":
            fields["country"] = "BY"
            if r["city"] and geo.locate(r["city"])[0] == "RU":
                fields.update(city="", region="")
        if fields:
            changed.append((r["id"], r["name"], dict(r), fields))
            if apply:
                db.update_fields(r["id"], fields)
                db.log(r["id"], "система", "город", "пересчёт: "
                       + ", ".join(f"{k}={v or '—'}" for k, v in fields.items()))
    print(f"Компаний в базе: {len(rows)}. Требуют правки: {len(changed)}")
    for _, name, was, now in changed[:20]:
        diff = ", ".join(f"{k}: {was.get(k) or '—'} -> {v or '—'}" for k, v in now.items())
        print(f"  {name[:30]:32} {diff}")
    if changed and not apply:
        print("\nЭто разбор без изменений. Применить: python app.py --recheck-cities --apply")
    elif apply:
        print("Изменения записаны в базу.")
    return {"total": len(rows), "changed": len(changed)}
