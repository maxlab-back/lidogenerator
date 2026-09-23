"""Контакты: мессенджеры и соцсети со страниц сайта, тип телефона, проверка почтовых доменов.

Мессенджеры для продаж в РФ/РБ часто быстрее звонка, поэтому тащим t.me / wa.me / viber / vk.
Почту проверяем по DNS: у домена должен быть MX (или хотя бы A) — иначе письмо не дойдёт.
"""
from __future__ import annotations

import re
import threading
from urllib.parse import unquote

import dns.exception
import dns.resolver

SOCIAL_RE = {
    "telegram": re.compile(r"https?://(?:t\.me|telegram\.me)/(?!share\b|iv\b)[A-Za-z0-9_+\-]{3,}(?:/[A-Za-z0-9_\-]+)?", re.I),
    "whatsapp": re.compile(r"(?:https?://(?:wa\.me/|api\.whatsapp\.com/send/?\?phone=)|whatsapp://send\?phone=)"
                           r"(?:%2B|\+)?\d{10,15}", re.I),
    "viber": re.compile(r"viber://(?:chat|add)\?number=(?:%2B|\+)?\d{10,15}", re.I),
    "vk": re.compile(r"https?://(?:m\.)?vk\.com/(?!share|away|widget|js/|images|video_ext|doc|wall|feed|search)"
                     r"[A-Za-z0-9_.]{2,}", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|explore|reel|stories)[A-Za-z0-9_.]{2,}", re.I),
    "ok": re.compile(r"https?://(?:www\.)?ok\.ru/(?:group|profile)/[A-Za-z0-9_.]+", re.I),
}
SOCIAL_TITLES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "viber": "Viber", "vk": "VK",
                 "instagram": "Instagram", "ok": "OK"}


_TG_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})(?=[/?#]|$)", re.I)
_TG_RESERVED = {"joinchat", "addstickers", "addemoji", "share", "iv", "proxy", "socks", "addlist",
                "boost", "login", "setlanguage", "contact"}


def tg_username(url: str) -> str:
    """@имя из ссылки t.me/имя, t.me/s/имя/123, telegram.me/имя; '' для инвайтов и служебных."""
    m = _TG_RE.match((url or "").strip())
    if not m:
        return ""
    u = m.group(1).lower()
    return "" if u in _TG_RESERVED else u


def social_key(kind: str, url: str) -> str:
    """Один контакт в разной записи → один ключ: wa.me/7999… и api.whatsapp.com/send?phone=+7999…,
    vk.ru/x и vk.com/x, telegram.me/x и t.me/x."""
    u = unquote(url or "").lower().strip()
    if kind in ("whatsapp", "viber"):
        return kind + ":" + re.sub(r"\D", "", u)[-10:]
    u = re.sub(r"^https?://(www\.|m\.)?", "", u).split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("vk.ru/", "vk.com/").replace("telegram.me/", "t.me/")
    return kind + ":" + u


def merge_socials(a: dict | None, b: dict | None) -> dict[str, list[str]]:
    out = {k: list(v) for k, v in (a or {}).items()}
    for kind, links in (b or {}).items():
        have = out.setdefault(kind, [])
        keys = {social_key(kind, x) for x in have}
        for x in links or []:
            k = social_key(kind, x)
            if k not in keys:
                keys.add(k)
                have.append(x)
    return {k: v for k, v in out.items() if v}


def extract_socials(html: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind, rx in SOCIAL_RE.items():
        seen: set[str] = set()
        vals: list[str] = []
        for m in rx.findall(html or ""):
            v = unquote(m).rstrip("/.,;'\")")
            key = social_key(kind, v)
            if key in seen:
                continue
            seen.add(key)
            vals.append(v)
            if len(vals) >= 3:
                break
        if vals:
            out[kind] = vals
    return out


def phone_kind(p: str) -> str:
    """моб / гор / 8-800 — продажнику важно, куда звонит."""
    if p.startswith("+7"):
        d = p[2:]
        if d.startswith("9"):
            return "моб"
        if d.startswith("800"):
            return "8-800"
        return "гор"
    if p.startswith("+375"):
        d = p[4:]
        if d[:2] in ("25", "29", "33", "44"):
            return "моб"
        if d.startswith("80"):
            return "8-80x"
        return "гор"
    return ""


# крупные почтовые сервисы — заведомо принимают почту, DNS не дёргаем
_KNOWN_MAIL = {"gmail.com", "mail.ru", "yandex.ru", "ya.ru", "yandex.by", "yandex.com", "bk.ru",
               "inbox.ru", "list.ru", "internet.ru", "rambler.ru", "outlook.com", "hotmail.com",
               "icloud.com", "me.com", "yahoo.com", "proton.me", "protonmail.com"}
_mx_cache: dict[str, bool] = {}
_mx_lock = threading.Lock()


def domain_accepts_mail(domain: str) -> bool:
    domain = domain.lower().strip(".")
    if domain in _KNOWN_MAIL:
        return True
    with _mx_lock:
        if domain in _mx_cache:
            return _mx_cache[domain]
    ok = True
    try:
        dns.resolver.resolve(domain, "MX", lifetime=4)
    except dns.resolver.NXDOMAIN:
        ok = False
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # MX нет — по RFC 5321 почта идёт на A-запись домена
        try:
            dns.resolver.resolve(domain, "A", lifetime=4)
        except dns.exception.DNSException:
            ok = False
    except dns.exception.DNSException:
        ok = True  # таймаут/сеть — не наказываем адрес за наши проблемы
    with _mx_lock:
        _mx_cache[domain] = ok
    return ok


def filter_emails(emails: list[str]) -> tuple[list[str], list[str]]:
    """-> (рабочие, отброшенные)."""
    good, bad = [], []
    for e in emails:
        dom = e.rsplit("@", 1)[-1]
        (good if domain_accepts_mail(dom) else bad).append(e)
    return good, bad


# УНП (Беларусь) из футера сайта — 9 цифр
UNP_RE = re.compile(r"УНП[\s:№]*?(\d{9})(?!\d)", re.I)
