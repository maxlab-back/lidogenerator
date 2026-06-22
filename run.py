#!/usr/bin/env python3
"""Точка входа лидогенератора.

Примеры:
    python run.py                       # по config.yaml
    python run.py --region Москва       # переопределить регион(ы)
    python run.py --region auto:60      # топ-60 городов из встроенного списка
    python run.py --config my.yaml      # другой конфиг
    python run.py --self-test           # проверить логику классификатора без сети
"""
from __future__ import annotations

import argparse

from parser.config import load_config, _resolve_regions
from parser.pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Поиск компаний-лидов по нишам")
    ap.add_argument("--config", default=None, help="путь к config.yaml")
    ap.add_argument("--region", action="append",
                    help="переопределить регион(ы): город, несколько --region или auto:NN")
    ap.add_argument("--backend", default=None, help="duckduckgo|google_cse|serper|yandex_xml")
    ap.add_argument("--self-test", action="store_true", help="оффлайн-проверка классификатора")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.region:
        # одиночный аргумент может быть "auto:NN" -> разворачиваем как в конфиге
        regs = args.region[0] if len(args.region) == 1 else args.region
        cfg["regions"] = _resolve_regions(regs)
    if args.backend:
        cfg.setdefault("sources", {}).setdefault("search", {})["backend"] = args.backend

    if args.self_test:
        from parser.selftest import run_self_test
        run_self_test(cfg)
        return

    run(cfg)


if __name__ == "__main__":
    main()
