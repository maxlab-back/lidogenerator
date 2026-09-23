"""Выгрузка лидов в CRM: Битрикс24 (входящий вебхук) и amoCRM (долгосрочный токен).

.env:
  BITRIX24_WEBHOOK=https://ваш-портал.bitrix24.ru/rest/1/xxxxxxxx/
  AMOCRM_HOST=ваш-аккаунт.amocrm.ru
  AMOCRM_TOKEN=долгосрочный токен интеграции
"""
from __future__ import annotations

import requests


def _comment(c: dict) -> str:
    parts = []
    if c.get("description"):
        parts.append(c["description"])
    if c.get("ai_reason"):
        parts.append("ИИ: " + c["ai_reason"])
    if c.get("rubrics"):
        parts.append("Рубрики: " + ", ".join(c["rubrics"]))
    for kind, links in (c.get("socials") or {}).items():
        parts.append(f"{kind}: " + ", ".join(links))
    if c.get("inn"):
        parts.append(f"ИНН/УНП: {c['inn']}")
    if c.get("comment"):
        parts.append("Комментарий: " + c["comment"])
    parts.append("Источник: лидогенератор")
    return "\n".join(parts)


class Bitrix24:
    name = "bitrix24"

    def __init__(self, webhook: str):
        self.base = webhook.rstrip("/") + "/"

    def push(self, companies: list[dict]) -> list[tuple[int, str, str]]:
        """-> [(id компании, id в CRM или '', ошибка или '')]."""
        out = []
        for c in companies:
            fields = {
                "TITLE": c.get("name") or c.get("website") or "Лид",
                "COMPANY_TITLE": c.get("legal_name") or c.get("name") or "",
                "PHONE": [{"VALUE": p, "VALUE_TYPE": "WORK"} for p in c.get("phones") or []],
                "EMAIL": [{"VALUE": e, "VALUE_TYPE": "WORK"} for e in c.get("emails") or []],
                "WEB": [{"VALUE": c["website"], "VALUE_TYPE": "WORK"}] if c.get("website") else [],
                "ADDRESS": c.get("address") or "",
                "ADDRESS_CITY": c.get("city") or "",
                "ADDRESS_REGION": c.get("region") or "",
                "COMMENTS": _comment(c),
                "SOURCE_ID": "WEB",
                "SOURCE_DESCRIPTION": "Лидогенератор",
            }
            try:
                r = requests.post(self.base + "crm.lead.add.json",
                                  json={"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}}, timeout=20)
                d = r.json()
                if r.ok and d.get("result"):
                    out.append((c["id"], str(d["result"]), ""))
                else:
                    out.append((c["id"], "", d.get("error_description") or f"HTTP {r.status_code}"))
            except (requests.RequestException, ValueError) as e:
                out.append((c["id"], "", str(e)))
        return out


class AmoCRM:
    name = "amocrm"

    def __init__(self, host: str, token: str):
        host = host.replace("https://", "").replace("http://", "").strip("/")
        self.url = f"https://{host}/api/v4/leads/complex"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def push(self, companies: list[dict]) -> list[tuple[int, str, str]]:
        out = []
        for i in range(0, len(companies), 50):          # лимит amoCRM — 50 сделок за запрос
            chunk = companies[i:i + 50]
            payload = []
            for c in chunk:
                cf = []
                if c.get("phones"):
                    cf.append({"field_code": "PHONE", "values": [{"value": p, "enum_code": "WORK"} for p in c["phones"]]})
                if c.get("emails"):
                    cf.append({"field_code": "EMAIL", "values": [{"value": e, "enum_code": "WORK"} for e in c["emails"]]})
                if c.get("website"):
                    cf.append({"field_code": "WEB", "values": [{"value": c["website"]}]})
                if c.get("address"):
                    cf.append({"field_code": "ADDRESS", "values": [{"value": c["address"]}]})
                company = {"name": c.get("legal_name") or c.get("name") or "Компания"}
                if cf:
                    company["custom_fields_values"] = cf
                payload.append({
                    "name": c.get("name") or "Лид",
                    "_embedded": {"companies": [company], "tags": [{"name": "лидогенератор"}]},
                })
            try:
                r = requests.post(self.url, json=payload, headers=self.headers, timeout=30)
                d = r.json() if r.content else {}
                if r.ok and isinstance(d, list):
                    for c, res in zip(chunk, d):
                        out.append((c["id"], str(res.get("id", "")), ""))
                else:
                    err = (d.get("title") or d.get("detail") or f"HTTP {r.status_code}") if isinstance(d, dict) else f"HTTP {r.status_code}"
                    out += [(c["id"], "", err) for c in chunk]
            except (requests.RequestException, ValueError) as e:
                out += [(c["id"], "", str(e)) for c in chunk]
        return out


def build_crms(keys: dict) -> dict:
    crms = {}
    if keys.get("bitrix24_webhook"):
        crms["bitrix24"] = Bitrix24(keys["bitrix24_webhook"])
    if keys.get("amocrm_host") and keys.get("amocrm_token"):
        crms["amocrm"] = AmoCRM(keys["amocrm_host"], keys["amocrm_token"])
    return crms
