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
    cfg["_keys"] = _keys_from_env()
    cfg["regions"] = _resolve_regions(cfg.get("regions"))
    return cfg


def _keys_from_env() -> dict:
    return {
        "google_cse_key": os.getenv("GOOGLE_CSE_KEY", "").strip(),
        "google_cse_cx": os.getenv("GOOGLE_CSE_CX", "").strip(),
        "serper": os.getenv("SERPER_API_KEY", "").strip(),
        "yandex_xml_user": os.getenv("YANDEX_XML_USER", "").strip(),
        "yandex_xml_key": os.getenv("YANDEX_XML_KEY", "").strip(),
        "dadata": os.getenv("DADATA_API_KEY", "").strip(),
        "apify": os.getenv("APIFY_TOKEN", "").strip(),
        # универсальный режим
        "anthropic": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "yandex_search_key": os.getenv("YANDEX_SEARCH_API_KEY", "").strip(),
        "yandex_folder": (os.getenv("YANDEX_FOLDER_ID") or os.getenv("YANDEX_XML_USER", "")).strip(),
        "bitrix24_webhook": os.getenv("BITRIX24_WEBHOOK", "").strip(),
        "amocrm_host": os.getenv("AMOCRM_HOST", "").strip(),
        "amocrm_token": os.getenv("AMOCRM_TOKEN", "").strip(),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "public_url": os.getenv("APP_PUBLIC_URL", "").strip(),
    }


def refresh_keys(cfg: dict) -> dict:
    """Перечитать .env после правки ключей из интерфейса — без перезапуска сервера.

    Словарь меняем на месте: на него ссылаются App и уже запущенные задачи. Ключи, которых
    в .env больше нет, убираем и из окружения процесса — иначе снятый ключ продолжал бы
    «работать» до перезапуска.
    """
    path = ROOT / ".env"
    present = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, _ = line.strip().partition("=")
            if sep and not name.startswith("#"):
                present.add(name.strip())
    for env_name in _ENV_OF.values():
        if env_name not in present:
            os.environ.pop(env_name, None)
    load_dotenv(path, override=True)
    cfg.setdefault("_keys", {}).update(_keys_from_env())
    return cfg["_keys"]


# обратное соответствие: ключ конфига -> переменная окружения (для снятия ключа)
_ENV_OF = {
    "google_cse_key": "GOOGLE_CSE_KEY", "google_cse_cx": "GOOGLE_CSE_CX", "serper": "SERPER_API_KEY",
    "dadata": "DADATA_API_KEY", "apify": "APIFY_TOKEN", "anthropic": "ANTHROPIC_API_KEY",
    "yandex_search_key": "YANDEX_SEARCH_API_KEY", "yandex_folder": "YANDEX_FOLDER_ID",
    "bitrix24_webhook": "BITRIX24_WEBHOOK", "amocrm_host": "AMOCRM_HOST",
    "amocrm_token": "AMOCRM_TOKEN", "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat": "TELEGRAM_CHAT_ID", "public_url": "APP_PUBLIC_URL",
    "yandex_xml_user": "YANDEX_XML_USER", "yandex_xml_key": "YANDEX_XML_KEY",
}


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
