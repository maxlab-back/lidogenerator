"""Ключи API: список сервисов, запись в .env и проверка ключа живым запросом.

Раньше ключи вписывались руками в .env, и на новой машине лидогенератор оказывался
полуслепым: без SERPER_API_KEY молча пропадали Google Карты (а это треть компаний —
те, у кого вообще нет сайта). Теперь всё то же самое делается из вкладки «Ключи»:
вставил ключ → «Проверить» → сохранилось в .env на сервере и заработало без перезапуска.

Проверка бесплатная везде, где у сервиса есть бесплатный служебный запрос
(остаток счёта, профиль). Где такого нет — честно пишем, что проверить нечем.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests

TIMEOUT = 15

# id -> описание сервиса. fields: (переменная в .env, подпись, это секрет?)
SERVICES = [
    {
        "id": "serper",
        "title": "Serper — Google Карты и поиск сайтов",
        "gives": "Google Карты: компании с телефоном и адресом, в том числе те, у кого нет сайта "
                 "(в текущей базе это треть всех компаний). Без ключа карт нет вообще, "
                 "а сайты ищутся через бесплатный DuckDuckGo.",
        "url": "https://serper.dev",
        "price": "2500 кредитов бесплатно при регистрации, дальше $50 за 50 000. "
                 "Расход — около 0.5 кредита на компанию.",
        "fields": [("SERPER_API_KEY", "Ключ API", True)],
    },
    {
        "id": "anthropic",
        "title": "Claude — ИИ-проверка компаний",
        "gives": "Читает сайт и решает, та ли это компания: отсекает каталоги, агрегаторы и СМИ, "
                 "которые проходят проверку по словам. Он же подбирает запросы кнопкой «Подобрать ИИ».",
        "url": "https://console.anthropic.com/settings/keys",
        "price": "По расходу токенов. На haiku проверка 200 компаний — центы, на opus — доллары.",
        "fields": [("ANTHROPIC_API_KEY", "Ключ API", True)],
    },
    {
        "id": "apify",
        "title": "Apify — Яндекс Карты и 2ГИС",
        "gives": "Второй источник карточек из карт, полезен по России вместе с Google Картами.",
        "url": "https://console.apify.com/settings/integrations",
        "price": "$5 в месяц бесплатно (≈ 700 компаний), дальше ≈ $7 за 1000. "
                 "2ГИС требует платного плана.",
        "fields": [("APIFY_TOKEN", "Токен", True)],
    },
    {
        "id": "dadata",
        "title": "DaData — юр. данные по России",
        "gives": "ИНН, юрлицо, статус, штат, выручка. Отсеивает ликвидированных и банкротов.",
        "url": "https://dadata.ru/profile/#info",
        "price": "10 000 запросов в сутки бесплатно.",
        "fields": [("DADATA_API_KEY", "Ключ API", True)],
    },
    {
        "id": "google_cse",
        "title": "Google CSE — бесплатный запасной поиск",
        "gives": "Когда кредиты Serper кончаются, сайты ищутся здесь, а не в DuckDuckGo: "
                 "это та же выдача Google.",
        "url": "https://programmablesearchengine.google.com/controlpanel/all",
        "price": "100 запросов в день бесплатно.",
        "fields": [("GOOGLE_CSE_KEY", "Ключ API", True), ("GOOGLE_CSE_CX", "Идентификатор движка (cx)", False)],
    },
    {
        "id": "yandex_search",
        "title": "Яндекс Поиск API — сайты из выдачи Яндекса",
        "gives": "По России выдача Яндекса полнее Google для местного бизнеса.",
        "url": "https://yandex.cloud/ru/docs/search-api/",
        "price": "По тарифу Yandex Cloud, оплата за запросы.",
        "fields": [("YANDEX_SEARCH_API_KEY", "Ключ сервисного аккаунта", True),
                   ("YANDEX_FOLDER_ID", "Идентификатор каталога", False)],
    },
    {
        "id": "telegram",
        "title": "Telegram — уведомления автопоиска",
        "gives": "Сообщение о новых компаниях по окончании поиска по расписанию.",
        "url": "https://t.me/BotFather",
        "price": "Бесплатно.",
        "fields": [("TELEGRAM_BOT_TOKEN", "Токен бота", True), ("TELEGRAM_CHAT_ID", "ID чата", False),
                   ("APP_PUBLIC_URL", "Адрес лидогенератора для ссылки в уведомлении", False)],
    },
    {
        "id": "bitrix24",
        "title": "Битрикс24 — выгрузка лидов",
        "gives": "Кнопка «В CRM» создаёт лиды прямо в портале.",
        "url": "https://helpdesk.bitrix24.ru/open/12357038/",
        "price": "Бесплатно, нужен входящий вебхук с правом crm.",
        "fields": [("BITRIX24_WEBHOOK", "Адрес вебхука", True)],
    },
    {
        "id": "amocrm",
        "title": "amoCRM — выгрузка лидов",
        "gives": "Кнопка «В CRM» создаёт сделки с компанией.",
        "url": "https://www.amocrm.ru/developers/content/oauth/step-by-step",
        "price": "Бесплатно, нужен долгосрочный токен интеграции.",
        "fields": [("AMOCRM_HOST", "Адрес аккаунта (ваш.amocrm.ru)", False),
                   ("AMOCRM_TOKEN", "Долгосрочный токен", True)],
    },
]

ENV_NAMES = [f[0] for s in SERVICES for f in s["fields"]]
OPTIONAL = {"APP_PUBLIC_URL"}          # без него сервис всё равно считается настроенным


# ----------------------------------------------------------------------------
# .env
# ----------------------------------------------------------------------------
def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Обновляет .env, сохраняя порядок строк и комментарии. Пустое значение убирает ключ."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        name = m.group(1) if m else ""
        if name in updates:
            done.add(name)
            if updates[name]:
                out.append(f"{name}={updates[name]}")
            # пустое значение — строку не пишем вовсе, ключ считается снятым
        else:
            out.append(line)
    out += [f"{n}={v}" for n, v in updates.items() if n not in done and v]
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    path.chmod(0o600)


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return value[:2] + "•" * max(2, len(value) - 2)
    return f"{value[:4]}…{value[-4:]}"


def state(path: Path) -> list[dict]:
    """Сервисы с отметкой, какие поля заполнены (значения — только в замаскированном виде)."""
    env = read_env(path)
    out = []
    for s in SERVICES:
        fields = [{"env": name, "label": label, "secret": secret,
                   "value": mask(env.get(name, "")) if secret else env.get(name, ""),
                   "set": bool(env.get(name))} for name, label, secret in s["fields"]]
        need = [f for f in fields if f["env"] not in OPTIONAL]
        out.append({**{k: v for k, v in s.items() if k != "fields"}, "fields": fields,
                    "ready": all(f["set"] for f in need)})
    return out


# ----------------------------------------------------------------------------
# проверка ключей живым запросом
# ----------------------------------------------------------------------------
def _serper(v: dict) -> tuple[bool, str]:
    r = requests.get("https://google.serper.dev/account",
                     headers={"X-API-KEY": v["SERPER_API_KEY"]}, timeout=TIMEOUT)
    if r.status_code == 403:
        return False, "ключ не принят"
    r.raise_for_status()
    bal = r.json().get("balance")
    return True, f"ключ рабочий, кредитов на счету: {bal}"


def _anthropic(v: dict) -> tuple[bool, str]:
    import anthropic
    try:
        models = anthropic.Anthropic(api_key=v["ANTHROPIC_API_KEY"], timeout=TIMEOUT).models.list(limit=3)
    except anthropic.AuthenticationError:
        return False, "ключ не принят"
    names = [m.id for m in getattr(models, "data", [])]
    return True, "ключ рабочий" + (f", доступны модели: {', '.join(names[:3])}" if names else "")


def _apify(v: dict) -> tuple[bool, str]:
    r = requests.get("https://api.apify.com/v2/users/me",
                     headers={"Authorization": f"Bearer {v['APIFY_TOKEN']}"}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        return False, "токен не принят"
    r.raise_for_status()
    d = (r.json().get("data") or {})
    plan = (d.get("plan") or {}).get("id") or "free"
    return True, f"токен рабочий, аккаунт {d.get('username') or '—'}, план {plan}"


def _dadata(v: dict) -> tuple[bool, str]:
    r = requests.post("https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party",
                      headers={"Authorization": f"Token {v['DADATA_API_KEY']}",
                               "Content-Type": "application/json"},
                      json={"query": "сбербанк", "count": 1}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        return False, "ключ не принят"
    r.raise_for_status()
    return True, "ключ рабочий"


def _google_cse(v: dict) -> tuple[bool, str]:
    r = requests.get("https://www.googleapis.com/customsearch/v1",
                     params={"key": v["GOOGLE_CSE_KEY"], "cx": v["GOOGLE_CSE_CX"], "q": "тест", "num": 1},
                     timeout=TIMEOUT)
    if r.status_code in (400, 403):
        return False, f"не принято: {r.json().get('error', {}).get('message', '')[:120]}"
    r.raise_for_status()
    return True, "ключ рабочий"


def _telegram(v: dict) -> tuple[bool, str]:
    r = requests.get(f"https://api.telegram.org/bot{v['TELEGRAM_BOT_TOKEN']}/getMe", timeout=TIMEOUT)
    if not r.ok:
        return False, "токен не принят"
    name = (r.json().get("result") or {}).get("username", "")
    chat = v.get("TELEGRAM_CHAT_ID")
    if chat:
        c = requests.get(f"https://api.telegram.org/bot{v['TELEGRAM_BOT_TOKEN']}/getChat",
                         params={"chat_id": chat}, timeout=TIMEOUT)
        if not c.ok:
            return False, f"бот @{name} рабочий, но чат {chat} недоступен — напиши боту первым " \
                          f"или добавь его в группу"
    return True, f"бот @{name} рабочий" + (", чат на месте" if chat else ", но ID чата не задан")


def _bitrix24(v: dict) -> tuple[bool, str]:
    base = v["BITRIX24_WEBHOOK"].rstrip("/")
    r = requests.get(f"{base}/profile.json", timeout=TIMEOUT)
    if not r.ok or "error" in (r.json() if r.content else {}):
        return False, "вебхук не принят"
    name = (r.json().get("result") or {}).get("NAME", "")
    return True, f"вебхук рабочий{', пользователь ' + name if name else ''}"


def _amocrm(v: dict) -> tuple[bool, str]:
    host = v["AMOCRM_HOST"].replace("https://", "").replace("http://", "").strip("/")
    r = requests.get(f"https://{host}/api/v4/account",
                     headers={"Authorization": f"Bearer {v['AMOCRM_TOKEN']}"}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        return False, "токен не принят"
    r.raise_for_status()
    return True, f"аккаунт {r.json().get('name', host)} на связи"


CHECKS = {"serper": _serper, "anthropic": _anthropic, "apify": _apify, "dadata": _dadata,
          "google_cse": _google_cse, "telegram": _telegram, "bitrix24": _bitrix24, "amocrm": _amocrm}


def check(service_id: str, path: Path) -> dict:
    """-> {ok, message}. ok=None — проверить нечем (у сервиса нет бесплатного запроса)."""
    svc = next((s for s in SERVICES if s["id"] == service_id), None)
    if not svc:
        raise ValueError("неизвестный сервис")
    env = read_env(path)
    missing = [name for name, _, _ in svc["fields"] if not env.get(name) and name not in OPTIONAL]
    if missing:
        return {"ok": False, "message": "не заполнено: " + ", ".join(missing)}
    fn = CHECKS.get(service_id)
    if not fn:
        return {"ok": None, "message": "у этого сервиса нет бесплатной проверки — "
                                       "ключ сохранён, проверится в первом же поиске"}
    try:
        ok, msg = fn(env)
    except requests.RequestException as e:
        return {"ok": False, "message": f"сервис не ответил: {e}"}
    except Exception as e:  # noqa: BLE001 — ответ чужого API может быть любым
        return {"ok": False, "message": f"не вышло проверить: {e}"}
    return {"ok": ok, "message": msg}
