"""Модель компании-лида."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Lead:
    name: str = ""
    city: str = ""
    address: str = ""
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    website: str = ""
    rubrics: list[str] = field(default_factory=list)
    source: str = ""              # search / apify_2gis
    source_url: str = ""          # ссылка на карточку
    region_hint: str = ""         # город из поискового запроса (подсказка для детекта)
    lat: Optional[float] = None
    lon: Optional[float] = None

    # заполняется классификатором
    lead_type: str = ""           # podokonnik_pvh / sandwich_otkos / ""
    lead_type_title: str = ""
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    site_confirmed: bool = False  # подтверждено ли по тексту сайта ("в размер" и т.п.)

    # юр.данные из DaData (ЕГРЮЛ)
    inn: str = ""
    ogrn: str = ""
    legal_name: str = ""          # полное наименование юрлица/ИП
    okved: str = ""               # основной ОКВЭД (код + название)
    manager: str = ""             # руководитель
    legal_address: str = ""
    dadata_matched: bool = False

    # технические поля для дедупликации
    raw_text: str = ""            # склеенный текст (имя+рубрики+сниппет сайта) для классификации

    def dedup_key(self) -> str:
        """Ключ для устранения дублей: телефон важнее всего, иначе имя+адрес."""
        if self.phones:
            digits = "".join(c for c in self.phones[0] if c.isdigit())
            if len(digits) >= 10:
                return "tel:" + digits[-10:]
        if self.website:
            return "site:" + _norm_domain(self.website)
        return "name:" + (self.name.lower().strip() + "|" + self.address.lower().strip())[:120]

    def to_row(self) -> dict:
        d = asdict(self)
        d["phones"] = ", ".join(self.phones[:8])   # для списка обзвона хватает; ограничиваем мега-ячейки
        d["emails"] = ", ".join(self.emails[:5])
        d["rubrics"] = ", ".join(self.rubrics)
        d["matched_keywords"] = ", ".join(self.matched_keywords)
        d.pop("raw_text", None)
        return d


def _norm_domain(url: str) -> str:
    u = url.lower().strip()
    for p in ("https://", "http://", "www."):
        if u.startswith(p):
            u = u[len(p):]
    return u.split("/")[0]


# Порядок и заголовки колонок для выгрузки — компактный список для обзвона.
COLUMNS = [
    ("name", "Название"),
    ("lead_type_title", "Тип лида"),
    ("score", "Уверенность"),
    ("city", "Город"),
    ("phones", "Телефоны"),
    ("emails", "Почты"),
    ("website", "Сайт"),
    ("source", "Источник"),
    ("source_url", "Ссылка на карточку"),
]
