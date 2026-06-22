"""Устранение дублей и слияние контактов."""
from __future__ import annotations

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


def _merge(a: list[str], b: list[str]) -> list[str]:
    seen, out = set(), []
    for x in [*a, *b]:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
