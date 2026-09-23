"""Обогащение лида по его сайту: вытаскиваем e-mail/телефоны и подтверждаем нишу.

Подтверждение ниши = на сайте встречаются слова про распил/нарезку/в размер,
а также целевые материалы (подоконник ПВХ / сэндвич-панель / откос).
"""
from __future__ import annotations

import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..htmlutil import parse_html
from ..models import Lead
from ..cities import detect_city
from ..contacts import UNP_RE, extract_socials, merge_socials

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# телефон из видимого текста с разделителями (высокая точность)
TEXT_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-]\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}(?!\d)")
# телефон слитной записью без разделителей: +79991234567 / 89991234567
SOLID_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)\d{10}(?!\d)")
# белорусский: +375 29 123-45-67, +375(29)1234567, 8 (029) 123-45-67, 8 0152 71-57-77
BY_PHONE_RE = re.compile(r"(?<![\d+])(?:\+\s?375|8\s?\(?0)(?:[\s\-()]*\d){9}(?!\d)")
# телефон из ссылки tel: (самый чистый источник)
TEL_HREF_RE = re.compile(r"tel:\s*(\+?[\d\s\-()]{10,20})")
# расширения файлов, ошибочно похожие на e-mail (retina-картинки logo@2x.png и т.п.)
ASSET_EXT = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico",
             "css", "js", "woff", "woff2", "ttf", "eot"}
# ИНН из текста сайта (обычно в футере) -> резко повышает матч в DaData
INN_RE = re.compile(r"ИНН[\s:№/]*?(\d{12}|\d{10})", re.IGNORECASE)


def normalize_phone(raw: str, country: str = "") -> str:
    """Каноничный вид: +7XXXXXXXXXX (РФ) или +375XXXXXXXXX (РБ). '' если не валиден.

    Белорусский внутренний формат «8 0XX …» отличаем от российского по нулю после
    восьмёрки: российские коды на 0 не начинаются.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("375"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("80"):
        return "+375" + digits[2:]
    if len(digits) == 9 and country == "BY":
        return "+375" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] != "0":
        return "+7" + digits
    return ""

def extract_phones(text_raw: str, html: str = "", country: str = "") -> list[str]:
    """Телефоны из tel:-ссылок + из текста (с разделителями, слитно, РБ) в каноничном виде."""
    raw_phones = (TEL_HREF_RE.findall(html)
                  + TEXT_PHONE_RE.findall(text_raw)
                  + SOLID_PHONE_RE.findall(text_raw)
                  + BY_PHONE_RE.findall(text_raw))
    phones: list[str] = []
    for raw in raw_phones:
        n = normalize_phone(raw, country)
        if n and n not in phones:
            phones.append(n)
    return phones


def extract_emails(text_raw: str, html: str = "") -> list[str]:
    """E-mail из видимого текста + mailto: (а не из всего HTML — иначе ловим logo@2x.png)."""
    mailtos = re.findall(r"mailto:([^\"'?>\s]+)", html)
    found = set(EMAIL_RE.findall(text_raw))
    found |= {m for m in mailtos if EMAIL_RE.fullmatch(m)}
    return sorted(e for e in found if not _is_junk_email(e))


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
    soup = parse_html(html)
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


def enrich_one(lead: Lead, timeout: int, max_pages: int, user_agent: str,
               keep_text: bool = False, cities: list[str] | None = None,
               renderer=None, proxies: list[str] | None = None) -> Lead:
    """keep_text — сохранить текст сайта в lead.site_text (для универсальной оценки);
    cities — словарь городов для детекта (по умолчанию — города РФ из cities.py);
    renderer — parser.render.Renderer: дорендерить JS-сайт, если главная пустая;
    proxies — список прокси, на сайт берётся случайный."""
    if not lead.website:
        return lead
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    proxy = random.choice(proxies) if proxies else ""
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    pages_text = []
    html = _fetch(session, lead.website, timeout)
    if renderer is not None:
        from ..render import needs_render
        visible = len(parse_html(html).get_text(" ", strip=True)) if html else 0
        if needs_render(html, visible):
            rendered = renderer.render(lead.website, proxy)
            if rendered and len(parse_html(rendered).get_text(" ", strip=True)) > visible:
                html = rendered
    if not html:
        return lead
    pages_text.append(html)

    for link in _inner_links(html, lead.website, max_pages - 1):
        t = _fetch(session, link, timeout)
        if t:
            pages_text.append(t)

    # извлекаем видимый текст + контакты из всего HTML
    full_html = "\n".join(pages_text)
    home_soup = parse_html(html)
    text_raw = parse_html(full_html).get_text(" ")
    text = _norm(text_raw)

    # e-mail берём из видимого текста + из mailto:-ссылок (а не из всего HTML,
    # чтобы не цеплять имена картинок вроде logo@2x.png из атрибутов src)
    emails = extract_emails(text_raw, full_html)
    phones = extract_phones(text_raw, full_html, lead.country)

    for e in emails:
        if e not in lead.emails:
            lead.emails.append(e)
    for p in phones:
        if p not in lead.phones:
            lead.phones.append(p)

    # ИНН из футера (для точного матча в DaData); для Беларуси — УНП
    if not lead.inn:
        m = INN_RE.search(text_raw) or UNP_RE.search(text_raw)
        if m:
            lead.inn = m.group(1)

    # мессенджеры и соцсети из ссылок
    if keep_text:
        lead.socials = merge_socials(lead.socials, extract_socials(full_html))

    # имя компании из сайта, если пусто
    if not lead.name:
        lead.name = _site_name(home_soup) or _domain_name(lead.website)

    # город: у лидов из карт уже задан — не трогаем; для сайтов смотрим адрес сайта и текст
    if not lead.city:
        lead.city = detect_city(text_raw, lead.region_hint, cities, url=lead.website)

    if keep_text:
        lead.site_text = text[:60000]
        # заголовок + meta description главной — «шапка» компании для оценки релевантности
        if not lead.description:
            title = home_soup.title.string.strip() if home_soup.title and home_soup.title.string else ""
            md = home_soup.find("meta", attrs={"name": "description"})
            meta = md["content"].strip() if md and md.get("content") else ""
            lead.description = " — ".join(x for x in (title, meta) if x)[:300]

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
        # og:site_name бывает рекламной фразой («Сфера — динамично развивающаяся компания…»):
        # берём часть до сильного разделителя; «ёлочки» не режем — в них обычно бренд
        name = re.split(r"\s[–—\-]\s|[|:·]", og["content"].strip())[0].strip()
        if name and len(name.split()) <= 6:
            return name[:120]
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
