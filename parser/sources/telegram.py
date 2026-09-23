"""Источник: Telegram-каналы компаний через поисковик (site:t.me) и публичные страницы t.me.

Без аккаунта Telegram и без риска бана: берём из выдачи ссылки на t.me, открываем витрину
(t.me/<имя>: название, описание, число подписчиков) и ленту последних постов (t.me/s/<имя>).
Выдача по site:t.me — в основном посты городских пабликов и СМИ, поэтому фильтруем строго:
группы (чаты) и боты пропускаются, каналы крупнее universal.telegram.max_subscribers — тоже.
Телефоны из постов берём, только если номер повторяется (чужие номера из отзывов — разовые).
"""
from __future__ import annotations

import re
from collections import Counter

import requests

from ..htmlutil import parse_html
from ..models import Lead
from ..relevance import norm
from .search import _is_company_site, registered_domain
from .website import extract_emails, extract_phones

# адрес сайта, записанный в описании канала текстом (без ссылки)
_URL_IN_TEXT = re.compile(r"(?:https?://)?(?:www\.)?((?:[a-zа-я0-9\-]+\.)+(?:ru|рф|by|бел|com|net|org|su|pro|shop|"
                          r"online|site|store|biz|info))(?:/[^\s<>\"']*)?", re.I)
_HEADERS = {"Accept-Language": "ru-RU,ru;q=0.9"}


def fetch_channel(session: requests.Session, username: str, timeout: int = 12) -> dict | None:
    try:
        r = session.get(f"https://t.me/{username}", headers=_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    soup = parse_html(r.text)
    t = soup.select_one(".tgme_page_title")
    if not t:
        return None                      # такого имени нет — t.me отдаёт общую заглушку
    extra_el = soup.select_one(".tgme_page_extra")
    extra = extra_el.get_text(" ", strip=True) if extra_el else ""
    d = soup.select_one(".tgme_page_description")
    low = extra.lower()
    if re.search(r"subscriber|подписчик", low):
        kind = "channel"
    elif re.search(r"member|участник", low):
        kind = "group"
    elif "bot" in low or username.endswith("bot"):
        kind = "bot"
    else:
        kind = "account"                 # бизнес-аккаунт / контакт компании
    digits = re.sub(r"\D", "", extra) if kind in ("channel", "group") else ""
    posts: list[str] = []
    if kind == "channel":
        try:
            r2 = session.get(f"https://t.me/s/{username}", headers=_HEADERS, timeout=timeout)
            if r2.status_code == 200:
                posts = [p.get_text(" ", strip=True)
                         for p in parse_html(r2.text).select(".tgme_widget_message_text")][-20:]
        except requests.RequestException:
            pass
    return {
        "username": username,
        "title": t.get_text(" ", strip=True),
        "kind": kind,
        "subscribers": int(digits) if digits else None,
        "description": d.get_text("\n", strip=True) if d else "",
        "links": [a.get("href", "") for a in d.find_all("a")] if d else [],
        "posts": posts,
    }


def channel_to_lead(ch: dict, country: str) -> Lead:
    desc = ch["description"]
    phones = extract_phones(desc, "", country)
    emails = extract_emails(desc)
    # в постах свой номер компания повторяет из поста в пост; разовые номера — чужие
    ph_cnt: Counter = Counter()
    em_cnt: Counter = Counter()
    for p in ch["posts"]:
        ph_cnt.update(extract_phones(p, "", country))
        em_cnt.update(extract_emails(p))
    phones += [n for n, c in ph_cnt.most_common() if c >= 2 and n not in phones]
    emails += [e for e, c in em_cnt.most_common() if c >= 2 and e not in emails]

    site = ""
    candidates = ch["links"] + ["https://" + m.group(1) for m in _URL_IN_TEXT.finditer(desc)]
    for u in candidates:
        if u.startswith(("http://", "https://")) and "t.me/" not in u and _is_company_site(u):
            site = "https://" + registered_domain(u)
            break

    url = f"https://t.me/{ch['username']}"
    subs = ch["subscribers"]
    rubric = {"channel": "Telegram-канал", "account": "Telegram-аккаунт"}.get(ch["kind"], "Telegram")
    if subs:
        rubric += ", " + f"{subs:,}".replace(",", " ") + " подписчиков"
    name = ch["title"] or "@" + ch["username"]
    if ch["kind"] == "account":
        # бизнес-аккаунт подписан именем владельца, а бренд — в описании: «Калуга Потолок»
        m = re.search(r"«([^»]{2,60})»|\"([^\"]{2,60})\"", desc)
        if m:
            name = m.group(1) or m.group(2)
    lead = Lead(
        name=name[:160],
        phones=phones[:6],
        emails=emails[:5],
        website=site,
        rubrics=[rubric],
        source="telegram",
        source_url=url,
        country=country,
        description=" ".join(desc.split())[:300],
    )
    lead.socials = {"telegram": [url]}
    lead.site_text = norm(" ".join([ch["title"], desc, *ch["posts"]]))[:60000]
    return lead
