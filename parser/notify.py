"""Уведомления в Telegram о новых лидах (для автопоиска).

.env: TELEGRAM_BOT_TOKEN (от @BotFather) и TELEGRAM_CHAT_ID (id чата/группы, куда слать).
"""
from __future__ import annotations

import html

import requests


def send_telegram(keys: dict, text: str) -> str:
    """-> '' при успехе, иначе текст ошибки."""
    token, chat = keys.get("telegram_token"), keys.get("telegram_chat")
    if not token or not chat:
        return "нет TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        return "" if r.ok else f"HTTP {r.status_code}: {r.text[:200]}"
    except requests.RequestException as e:
        return str(e)


def run_summary(title: str, new_rows: list[dict], total: int, url: str = "") -> str:
    e = html.escape
    lines = [f"🎯 <b>{e(title)}</b>", f"Новых компаний: <b>{len(new_rows)}</b> (всего в поиске {total})"]
    for r in new_rows[:10]:
        phone = (r.get("phones") or [""])[0]
        where = r.get("city") or r.get("region") or ""
        lines.append(f"• {e(r.get('name') or '')} — {e(where)} {e(phone)}")
    if len(new_rows) > 10:
        lines.append(f"…и ещё {len(new_rows) - 10}")
    if url:
        lines.append(e(url))
    return "\n".join(lines)
