"""Классификация компании в тип лида по ключевым словам (скоринг)."""
from __future__ import annotations

import re
from functools import lru_cache

from .models import Lead


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


@lru_cache(maxsize=4096)
def _pattern(word: str) -> re.Pattern:
    # совпадение только в начале слова (stem-матч): "резка" НЕ матчит "нарезка",
    # но "пластиков" по-прежнему матчит "пластиковые".
    return re.compile(r"(?<!\w)" + re.escape(word))


def _has(text: str, word: str) -> bool:
    return _pattern(_norm(word)).search(text) is not None


def classify(lead: Lead, lead_types: dict) -> Lead:
    """Проставляет lead_type / score / matched_keywords. Берёт лучший по score тип."""
    text = _norm(lead.raw_text or " ".join([lead.name, *lead.rubrics]))

    best = None
    for key, rules in lead_types.items():
        must = rules.get("must") or []
        any_words = rules.get("any") or []
        boost = rules.get("boost") or []
        block = rules.get("block") or []

        # вырожденный тип (нет ни must, ни any) матчил бы вообще всё — пропускаем
        if not must and not any_words:
            continue
        # блок-слова сразу выкидывают
        if any(_has(text, w) for w in block):
            continue
        # обязательные слова
        if must and not all(_has(text, w) for w in must):
            continue
        # хотя бы одно из any (если any пуст — считаем условие выполненным)
        any_hits = [w for w in any_words if _has(text, w)]
        if any_words and not any_hits:
            continue

        matched = []
        matched += [w for w in must if _has(text, w)]
        matched += any_hits
        boost_hits = [w for w in boost if _has(text, w)]
        matched += boost_hits

        # скоринг: базовая релевантность за прохождение must/any/block,
        # затем бонус за признаки "в размер" и за подтверждение сайтом.
        score = 0.40
        score += min(len(boost_hits) * 0.12, 0.35)
        if lead.site_confirmed:
            score += 0.15
        score = round(min(score, 1.0), 2)

        if best is None or score > best[1]:
            best = (key, score, rules.get("title", key), matched)

    if best:
        lead.lead_type, lead.score, lead.lead_type_title, lead.matched_keywords = best
        # уникализируем слова, сохраняя порядок
        seen = set()
        lead.matched_keywords = [w for w in lead.matched_keywords if not (w in seen or seen.add(w))]
    return lead
