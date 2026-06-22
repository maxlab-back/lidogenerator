"""Обогащение лида по его сайту: вытаскиваем e-mail/телефоны и подтверждаем нишу.

Подтверждение ниши = на сайте встречаются слова про распил/нарезку/в размер,
а также целевые материалы (подоконник ПВХ / сэндвич-панель / откос).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..models import Lead
from ..cities import detect_city

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# телефон из видимого текста с разделителями (высокая точность)
TEXT_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-]\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}(?!\d)")
# телефон слитной записью без разделителей: +79991234567 / 89991234567
SOLID_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)\d{10}(?!\d)")
# телефон из ссылки tel: (самый чистый источник)
TEL_HREF_RE = re.compile(r"tel:\s*(\+?[\d\s\-()]{10,20})")
# расширения файлов, ошибочно похожие на e-mail (retina-картинки logo@2x.png и т.п.)
ASSET_EXT = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico",
             "css", "js", "woff", "woff2", "ttf", "eot"}
# ИНН из текста сайта (обычно в футере) -> резко повышает матч в DaData
INN_RE = re.compile(r"ИНН[\s:№/]*?(\d{12}|\d{10})", re.IGNORECASE)


def normalize_phone(raw: str) -> str:
    """Приводит телефон к каноничному +7XXXXXXXXXX. Возвращает '' если не валиден."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return ""
    return "+" + digits

# ссылки на внутренние страницы, которые стоит дочитать
INNER_HINTS = ("contact", "kontakt", "контакт", "about", "o-", "услуг", "uslug",
               "price", "цен", "podokon", "подокон", "otkos", "откос", "sendvich",
               "sandwich", "сэндвич", "сендвич", "catalog", "katalog", "продукц")

# слова, подтверждающие "режут в размер"
SIZE_WORDS = ("в размер", "по размеру", "в нарезку", "нарезк", "распил", "порезк",
              "под заказ", "на заказ", "в нужный размер", "по вашим размерам",
              "по индивидуальным размерам", "режем", "пилим", "раскрой")

# целевые материалы — чтобы не подтверждать случайные сайты
TARGET_WORDS = ("подоконник", "сэндвич", "сендвич", "откос", "пвх", "пластиков")


def _norm(t: str) -> str:
    return " ".join(t.lower().replace("ё", "е").split())


def _fetch(session: requests.Session, url: str, timeout: int) -> str:
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            return ""
        ct = r.headers.get("Content-Type", "")
        if "html" not in ct and "text" not in ct:
            return ""
        return r.text
    except requests.RequestException:
        return ""


def _inner_links(html: str, base_url: str, limit: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_dom = urlparse(base_url).netloc
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        if urlparse(full).netloc != base_dom:
            continue
        low = full.lower()
        if any(h in low for h in INNER_HINTS) and full not in seen:
            seen.add(full)
            found.append(full)
        if len(found) >= limit:
            break
    return found


def enrich_one(lead: Lead, timeout: int, max_pages: int, user_agent: str) -> Lead:
    if not lead.website:
        return lead
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })

    pages_text = []
    html = _fetch(session, lead.website, timeout)
    if not html:
        return lead
    pages_text.append(html)

    for link in _inner_links(html, lead.website, max_pages - 1):
        t = _fetch(session, link, timeout)
        if t:
            pages_text.append(t)

    # извлекаем видимый текст + контакты из всего HTML
    full_html = "\n".join(pages_text)
    home_soup = BeautifulSoup(html, "lxml")
    text_raw = BeautifulSoup(full_html, "lxml").get_text(" ")
    text = _norm(text_raw)

    # e-mail берём из видимого текста + из mailto:-ссылок (а не из всего HTML,
    # чтобы не цеплять имена картинок вроде logo@2x.png из атрибутов src)
    mailtos = re.findall(r"mailto:([^\"'?>\s]+)", full_html)
    found_emails = set(EMAIL_RE.findall(text_raw))
    found_emails |= {m for m in mailtos if EMAIL_RE.fullmatch(m)}
    emails = sorted(e for e in found_emails if not _is_junk_email(e))

    # телефоны: из tel:-ссылок + из текста (с разделителями и слитно), всё в каноничный вид
    raw_phones = (TEL_HREF_RE.findall(full_html)
                  + TEXT_PHONE_RE.findall(text_raw)
                  + SOLID_PHONE_RE.findall(text_raw))
    phones = []
    seen_ph = set()
    for raw in raw_phones:
        norm = normalize_phone(raw)
        if norm and norm not in seen_ph:
            seen_ph.add(norm)
            phones.append(norm)

    for e in emails:
        if e not in lead.emails:
            lead.emails.append(e)
    for p in phones:
        if p not in lead.phones:
            lead.phones.append(p)

    # ИНН из футера (для точного матча в DaData)
    if not lead.inn:
        m = INN_RE.search(text_raw)
        if m:
            lead.inn = m.group(1)

    # имя компании из сайта, если пусто
    if not lead.name:
        lead.name = _site_name(home_soup) or _domain_name(lead.website)

    # город: для лидов из карт (2ГИС) уже задан — не трогаем; для сайтов определяем по тексту
    if not lead.city:
        lead.city = detect_city(text_raw, lead.region_hint)

    # подтверждение ниши
    has_size = any(w in text for w in SIZE_WORDS)
    has_target = any(w in text for w in TARGET_WORDS)
    if has_size and has_target:
        lead.site_confirmed = True

    # текст для классификатора: имя + все релевантные слова, встретившиеся на сайте
    snippet = " ".join(w for w in (SIZE_WORDS + TARGET_WORDS) if w in text)
    lead.raw_text = " ".join([lead.name, lead.raw_text, snippet]).strip()
    return lead


# слова-маркеры «это рекламный заголовок страницы, а не название фирмы»
_MARKETING_WORDS = ("купить", "цена", "цены", "ценам", "недорого", "доставка", "доставкой",
                    "производител", "производства", "магазин", "каталог", "официальн",
                    "заказать", "низким", "акци", "скидк", "оптом", "в москве", "в спб")


def _site_name(soup: BeautifulSoup) -> str:
    """Название фирмы из сайта. og:site_name — самый надёжный бренд; иначе кусок <title>.

    Заголовок режем только по СИЛЬНЫМ разделителям (| » : и дефис/тире В ОКРУЖЕНИИ ПРОБЕЛОВ),
    чтобы не разорвать «Интернет-магазин» по внутрисловному дефису. Если получился рекламный
    заголовок (длинный или со словами купить/цены/доставка) — это не бренд, вернём пусто,
    и вызывающий код подставит домен (стабильный идентификатор, без мусорных запросов в DaData).
    """
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and og.get("content"):
        return og["content"].strip()[:120]
    if soup.title and soup.title.string:
        t = re.split(r"\s[–—\-]\s|[|»«:▸·]", soup.title.string.strip())[0].strip()
        low = t.lower()
        if len(t.split()) >= 5 or any(m in low for m in _MARKETING_WORDS):
            return ""
        return t[:120]
    return ""


def _domain_name(url: str) -> str:
    from .search import registered_domain
    return registered_domain(url)


_JUNK_EMAILS = {"example@example.com", "info@site.ru"}


def _is_junk_email(e: str) -> bool:
    e = e.lower()
    if e in _JUNK_EMAILS:
        return True
    # домен заканчивается на расширение файла -> это не почта (logo@2x.png и т.п.)
    return e.rsplit(".", 1)[-1] in ASSET_EXT


def enrich_leads(leads: list[Lead], cfg: dict, user_agent: str) -> list[Lead]:
    timeout = cfg.get("timeout", 12)
    max_pages = cfg.get("max_pages_per_site", 4)
    workers = cfg.get("max_workers", 8)

    targets = [l for l in leads if l.website]
    if not targets:
        return leads

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(enrich_one, l, timeout, max_pages, user_agent): l for l in targets}
        done = 0
        errors = 0
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                errors += 1
                if errors <= 10:
                    print(f"    ! сайт {futures[fut].website}: {e}")
            done += 1
            if done % 25 == 0:
                print(f"    обогащено сайтов: {done}/{len(targets)}")
    return leads
