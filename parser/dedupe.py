"""Устранение дублей и слияние контактов."""
from __future__ import annotations

from .contacts import merge_socials, tg_username
from .models import Lead


def dedupe(leads: list[Lead]) -> list[Lead]:
    merged: dict[str, Lead] = {}
    for lead in leads:
        key = lead.dedup_key()
        if key not in merged:
            merged[key] = lead
            continue
        # сливаем контакты в уже существующий лид
        base = merged[key]
        base.phones = _merge(base.phones, lead.phones)
        base.emails = _merge(base.emails, lead.emails)
        base.rubrics = _merge(base.rubrics, lead.rubrics)
        if not base.website and lead.website:
            base.website = lead.website
        if not base.address and lead.address:
            base.address = lead.address
        base.site_confirmed = base.site_confirmed or lead.site_confirmed
        base.raw_text = (base.raw_text + " " + lead.raw_text).strip()
    return list(merged.values())


def merge_leads(leads: list[Lead]) -> list[Lead]:
    """Склейка дублей для универсального режима: один лид = общий телефон ИЛИ общий домен.

    В отличие от dedupe() (ключ — только первый телефон) ловит случаи, когда карточка
    из карт и сайт из выдачи делят любой из телефонов. Базой остаётся лид с большим score.
    """
    parent = list(range(len(leads)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, l in enumerate(leads):
        keys = ["tel:" + "".join(c for c in p if c.isdigit()) for p in l.phones]
        if l.website:
            keys.append("site:" + l.dedup_key_domain())
        keys += ["tg:" + tg_username(u) for u in l.socials.get("telegram", []) if tg_username(u)]
        for k in keys:
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i

    groups: dict[int, list[Lead]] = {}
    for i, l in enumerate(leads):
        groups.setdefault(find(i), []).append(l)

    out: list[Lead] = []
    for members in groups.values():
        members.sort(key=lambda l: l.score, reverse=True)
        base = members[0]
        for other in members[1:]:
            base.phones = _merge(base.phones, other.phones)
            base.emails = _merge(base.emails, other.emails)
            base.rubrics = _merge(base.rubrics, other.rubrics)
            base.matched_keywords = _merge(base.matched_keywords, other.matched_keywords)
            base.ai_services = _merge(base.ai_services, other.ai_services)
            base.socials = merge_socials(base.socials, other.socials)
            for f in ("website", "address", "rating", "city", "region", "country",
                      "description", "lat", "lon", "inn", "ogrn", "legal_name", "okved", "manager",
                      "legal_status", "employees", "revenue", "ai_verdict", "ai_type", "ai_reason",
                      "ai_confidence"):
                if not getattr(base, f) and getattr(other, f):
                    setattr(base, f, getattr(other, f))
            if other.source not in base.source.split("+"):
                base.source = base.source + "+" + other.source
        out.append(base)
    return out


def _merge(a: list[str], b: list[str]) -> list[str]:
    seen, out = set(), []
    for x in [*a, *b]:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
