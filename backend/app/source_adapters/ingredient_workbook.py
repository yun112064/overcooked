"""Read the exact T02 ingredient universe from ``ingredient_aliases`` only."""

from __future__ import annotations

import unicodedata
from collections import OrderedDict
from pathlib import Path

from openpyxl import load_workbook

from .price_models import IngredientQuery


SHEET_NAME = "ingredient_aliases"
REQUIRED_COLUMNS = {"canonical_name", "Food_ID", "alias"}


def _clean(value: object) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def load_ingredient_queries(path: str | Path) -> list[IngredientQuery]:
    """Return canonical ingredients in first-seen order, with aliases grouped.

    No other worksheet is opened or inspected.  Blank canonical names are
    ignored; aliases and Food_ID values can only decorate an existing canonical
    row and never create a new query.
    """

    workbook = load_workbook(
        filename=Path(path), read_only=True, data_only=True
    )
    try:
        if SHEET_NAME not in workbook.sheetnames:
            raise ValueError(f"required worksheet not found: {SHEET_NAME}")
        sheet = workbook[SHEET_NAME]
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = [_clean(value) for value in next(rows)]
        except StopIteration as exc:
            raise ValueError(f"worksheet {SHEET_NAME} is empty") from exc

        missing = REQUIRED_COLUMNS.difference(headers)
        if missing:
            raise ValueError(
                "ingredient_aliases missing required columns: "
                + ", ".join(sorted(missing))
            )
        indexes = {name: headers.index(name) for name in REQUIRED_COLUMNS}
        grouped: OrderedDict[str, dict[str, list[str]]] = OrderedDict()
        for row in rows:
            canonical = _clean(row[indexes["canonical_name"]])
            if not canonical:
                continue
            values = grouped.setdefault(canonical, {"food_ids": [], "aliases": []})
            food_id = _clean(row[indexes["Food_ID"]])
            alias = _clean(row[indexes["alias"]])
            if food_id and food_id not in values["food_ids"]:
                values["food_ids"].append(food_id)
            if alias and alias != canonical and alias not in values["aliases"]:
                values["aliases"].append(alias)
    finally:
        workbook.close()

    return [
        IngredientQuery(canonical_name=name, **metadata)
        for name, metadata in grouped.items()
    ]
