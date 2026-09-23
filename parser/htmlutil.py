"""Разбор HTML одной точкой входа: lxml, если он есть, иначе встроенный html.parser.

lxml быстрее и спокойнее относится к битой вёрстке, поэтому он первый выбор. Но его
колесо собирается не везде (musl-дистрибутивы, редкие архитектуры, старые питоны), и
ради одного парсера ронять весь лидогенератор незачем — тогда работаем на stdlib.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

try:
    BeautifulSoup("", "lxml")
    PARSER = "lxml"
except Exception:  # noqa: BLE001 — bs4 бросает FeatureNotFound, но версии отличаются
    PARSER = "html.parser"


def parse_html(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup or "", PARSER)
