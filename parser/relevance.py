"""Универсальная оценка релевантности лида: ключевые слова + контекст + минус-слова.

Никаких словарей ниш — всё строится из того, что ввёл пользователь:
  keywords — фразы запросов («ремонт квартир под ключ»). Считаем покрытие фразы
             основами слов в «шапке» компании (название/заголовок/описание/рубрика)
             и в тексте сайта.
  context  — свободное описание («делают кухни на заказ, свой цех»); доля его
             основ, найденных на сайте.
  minus    — слово/фраза в шапке -> лид отсеивается (score = 0).
Основы — лёгкий стеммер: отрезаем падежное окончание, матчим по началу слова.
"""
from __future__ import annotations

import re
from functools import lru_cache

# служебные и «коммерческие» слова — не несут смысла ниши
STOP = {
    "в", "во", "на", "по", "для", "и", "или", "с", "со", "от", "до", "из", "под", "за",
    "к", "ко", "у", "о", "об", "при", "без", "над", "а", "но", "не", "что", "как", "это",
    "купить", "цена", "цены", "стоимость", "недорого", "дешево", "заказать", "услуги",
    "услуга", "компания", "компании", "фирма", "лучшие", "рядом", "официальный", "сайт",
    "город", "г", "область", "обл", "район", "все", "мы", "наш", "наши", "которые",
    "который", "ищу", "нужны", "нужен", "занимаются", "занимается",
}

# окончания от длинных к коротким; основа после отрезания — не короче 4 букв
_ENDINGS = sorted([
    "иями", "ями", "ами", "иях", "ях", "ах", "ого", "его", "ому", "ему", "ыми", "ими",
    "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ов", "ев",
    "ам", "ям", "ом", "ем", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь",
], key=len, reverse=True)

_WORD_RE = re.compile(r"[a-zа-я0-9]+")


def norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if len(w) <= 4:
        return w
    for end in _ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[: -len(end)]
    return w


def terms(text: str) -> list[str]:
    """Значимые основы слов фразы, без повторов (порядок сохраняется)."""
    out: list[str] = []
    for w in _WORD_RE.findall(norm(text)):
        if w in STOP or (len(w) < 2) or w.isdigit():
            continue
        s = stem(w)
        if s not in out:
            out.append(s)
    return out


@lru_cache(maxsize=8192)
def _pattern(term: str) -> re.Pattern:
    # короткие основы (пвх, it, seo) — только целым словом, длинные — по началу слова
    if len(term) <= 3:
        return re.compile(r"(?<![a-zа-я0-9])" + re.escape(term) + r"(?![a-zа-я0-9])")
    return re.compile(r"(?<![a-zа-я0-9])" + re.escape(term))


def has(text: str, term: str) -> bool:
    return _pattern(term).search(text) is not None


def _phrase_cover(text: str, phrase_terms: list[str]) -> tuple[float, list[str]]:
    if not phrase_terms:
        return 0.0, []
    hits = [t for t in phrase_terms if has(text, t)]
    return len(hits) / len(phrase_terms), hits


class Profile:
    """Скомпилированный поисковый профиль — считается один раз на задачу."""

    def __init__(self, keywords: list[str], context: str = "", minus: list[str] | None = None):
        self.phrases = [t for t in (terms(k) for k in keywords) if t]
        self.context = terms(context)
        self.minus = [norm(m) for m in (minus or []) if norm(m)]

    def head_hit(self, text: str) -> bool:
        """Есть ли в тексте хоть одна основа из ключевых фраз."""
        t = norm(text)
        return any(has(t, term) for ph in self.phrases for term in ph)

    def score(self, head: str, body: str, from_maps: bool = False) -> tuple[float, list[str], str]:
        """-> (score 0..1, совпавшие основы, причина отсева или '')."""
        head, body = norm(head), norm(body)
        for m in self.minus:
            # минус-фраза: все её слова в шапке
            mt = terms(m) or [m]
            if all(has(head, t) for t in mt):
                return 0.0, [], f"минус-слово: {m}"

        matched: list[str] = []
        head_cov = body_cov = 0.0
        for ph in self.phrases:
            c, hits = _phrase_cover(head, ph)
            head_cov = max(head_cov, c)
            matched += hits
            if body:
                c, hits = _phrase_cover(body, ph)
                body_cov = max(body_cov, c)
                matched += hits
        kw = max(head_cov, 0.7 * body_cov)

        if self.context:
            full = head + " " + body
            ctx_hits = [t for t in self.context if has(full, t)]
            matched += ctx_hits
            ctx = min(1.0, 1.5 * len(ctx_hits) / len(self.context))
        else:
            ctx = kw

        # карты сами отдали компанию по запросу — это уже сигнал релевантности
        base = 0.30 if from_maps else 0.15
        score = round(min(1.0, base + 0.45 * kw + 0.40 * ctx), 2)
        seen: set[str] = set()
        matched = [t for t in matched if not (t in seen or seen.add(t))]
        return score, matched, ""
