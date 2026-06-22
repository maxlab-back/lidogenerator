"""Оффлайн-проверка классификатора (без сети) — убедиться, что правила работают."""
from __future__ import annotations

from .classify import classify
from .models import Lead

# (текст карточки, подтверждено_сайтом, ожидаемый_тип)
CASES = [
    ("Подоконники ПВХ нарезка в размер", True, "podokonnik_pvh"),
    ("Подоконники пластиковые на заказ, распил", False, "podokonnik_pvh"),
    ("Сэндвич-панели для откосов в размер", True, "sandwich_otkos"),
    ("Сендвич панель откосы нарезка под заказ", False, "sandwich_otkos"),
    ("Деревянные подоконники из массива дуба", False, ""),          # block -> не подоконник_пвх
    ("Кровельные сэндвич-панели для ангаров", False, ""),           # block -> не sandwich
    ("Кафе Ромашка, кофе и десерты", False, ""),                    # мимо
    ("Окна пластиковые, подоконники ПВХ, откосы сэндвич в размер", True, None),  # любой из двух
]


def run_self_test(cfg: dict) -> None:
    lead_types = cfg.get("lead_types", {})
    ok = 0
    print("Самопроверка классификатора:\n")
    for text, confirmed, expected in CASES:
        l = Lead(name=text, raw_text=text, site_confirmed=confirmed)
        classify(l, lead_types)
        got = l.lead_type or ""
        status = "?"
        if expected is None:
            passed = got != ""           # должен попасть хоть в какой-то тип
        else:
            passed = got == expected
        status = "OK " if passed else "FAIL"
        ok += int(passed)
        print(f"  [{status}] «{text[:48]:48}» -> {got or '—':16} "
              f"score={l.score} kw={l.matched_keywords}")
    print(f"\nИтог: {ok}/{len(CASES)} прошло.")
