"""Загрузка конфига и ключей."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_keys"] = {
        "google_cse_key": os.getenv("GOOGLE_CSE_KEY", "").strip(),
        "google_cse_cx": os.getenv("GOOGLE_CSE_CX", "").strip(),
        "serper": os.getenv("SERPER_API_KEY", "").strip(),
        "yandex_xml_user": os.getenv("YANDEX_XML_USER", "").strip(),
        "yandex_xml_key": os.getenv("YANDEX_XML_KEY", "").strip(),
        "dadata": os.getenv("DADATA_API_KEY", "").strip(),
        "apify": os.getenv("APIFY_TOKEN", "").strip(),
    }
    cfg["regions"] = _resolve_regions(cfg.get("regions"))
    return cfg


def _resolve_regions(regions) -> list[str]:
    """Поддержка 'auto:NN' и списка/пустого значения."""
    from .cities import CITIES
    if not regions:
        return []
    if isinstance(regions, str):
        s = regions.strip()
        if s.lower().startswith("auto:"):
            try:
                n = int(s.split(":", 1)[1])
            except ValueError:
                n = 40
            return CITIES[:n]
        return [s]
    return list(regions)
