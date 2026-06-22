"""Выгрузка лидов в Excel (вкладки по типам) и CSV."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .models import Lead, COLUMNS

SHEET_NAMES = {
    "podokonnik_pvh": "Подоконник ПВХ",
    "sandwich_otkos": "Сэндвич-панель откосы",
    "_maybe": "Под вопросом",
}


def _frame(leads: list[Lead]) -> pd.DataFrame:
    rows = [l.to_row() for l in leads]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=[c[1] for c in COLUMNS])
    keys = [c[0] for c in COLUMNS]
    df = df[[k for k in keys if k in df.columns]]
    df = df.rename(columns=dict(COLUMNS))
    # сортируем по уверенности
    if "Уверенность" in df.columns:
        df = df.sort_values("Уверенность", ascending=False)
    return df


def export(leads: list[Lead], cfg: dict) -> dict:
    out_cfg = cfg.get("output", {})
    out_dir = Path(cfg.get("output", {}).get("dir", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    threshold = cfg.get("score_threshold", 0.45)
    include_maybe = out_cfg.get("include_maybe", True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    # группируем
    groups: dict[str, list[Lead]] = {}
    maybe: list[Lead] = []
    for l in leads:
        if not l.lead_type or l.score < threshold:
            maybe.append(l)
        else:
            groups.setdefault(l.lead_type, []).append(l)

    written = {"counts": {}, "files": []}

    if out_cfg.get("excel", True):
        xlsx = out_dir / f"leads_{stamp}.xlsx"
        with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
            any_sheet = False
            for key, sheet in SHEET_NAMES.items():
                if key == "_maybe":
                    data = maybe if include_maybe else []
                else:
                    data = groups.get(key, [])
                if not data and key != "_maybe":
                    continue
                if key == "_maybe" and not include_maybe:
                    continue
                _frame(data).to_excel(writer, sheet_name=sheet[:31], index=False)
                written["counts"][sheet] = len(data)
                any_sheet = True
            if not any_sheet:
                pd.DataFrame(columns=["нет данных"]).to_excel(writer, sheet_name="Пусто", index=False)
        written["files"].append(str(xlsx))

    if out_cfg.get("csv", True):
        for key, data in groups.items():
            name = SHEET_NAMES.get(key, key)
            path = out_dir / f"{key}_{stamp}.csv"
            _frame(data).to_csv(path, index=False, encoding="utf-8-sig")
            written["files"].append(str(path))
            written["counts"].setdefault(name, len(data))
        if include_maybe and maybe:
            path = out_dir / f"maybe_{stamp}.csv"
            _frame(maybe).to_csv(path, index=False, encoding="utf-8-sig")
            written["files"].append(str(path))
            written["counts"].setdefault(SHEET_NAMES["_maybe"], len(maybe))

    return written
