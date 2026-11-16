"""User-decided price supplements for foods no store quote covers (config/t02_price_supplements.json).

Decided by the user on 2026-11-16:
- derived: price a food from another food's live quote at a stated mass ratio (水煮蛋 → 雞蛋 1:1,
  蛋黃 → 雞蛋 by yolk share, 香菇水 → 乾香菇 by soaking ratio). A derived food is "included" (cost 0)
  when the menu already buys one of its ``included_if_in_menu`` foods, so the source is not paid twice.
- estimates: a cited, dated retail or market figure used when nothing better exists. Estimates count in
  cost and budget but are always labelled 估價 with their source and date; they never become a store quote.
  ``whole_package`` estimates charge one package whatever the recipe needs.

Nothing here invents a figure: every rule carries its basis and source URLs, and loading fails on a
malformed rule rather than pricing with it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

SUPPLEMENTS_PATH = Path(__file__).resolve().parents[3] / "config" / "t02_price_supplements.json"
FOOD = re.compile(r"FOOD\d{6}")


@dataclass(frozen=True)
class DerivedRule:
    food_id: str
    name: str
    source_food_id: str
    source_name: str
    ratio: Decimal          # grams of the source food per gram of this food
    included_if_in_menu: tuple[str, ...]
    basis: str
    estimate: bool = False  # the conversion is approximate: label the line 估價 even at ratio 1


@dataclass(frozen=True)
class EstimateRule:
    food_id: str
    name: str
    price: Decimal
    package_quantity: Decimal
    package_unit: str       # always "g": volumes are converted in the rule's basis
    whole_package: bool
    observed: str
    source_name: str
    source_url: str
    basis: str


@dataclass(frozen=True)
class PriceSupplements:
    derived: dict[str, DerivedRule]
    estimates: dict[str, EstimateRule]


def _decimal(value, field: str, food_id: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field} for {food_id}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be positive for {food_id}")
    return number


def _text(item: dict, field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {field} for {item.get('food_id')}")
    return value.strip()


def parse_supplements(data: dict) -> PriceSupplements:
    if data.get("version") != 1:
        raise ValueError("Unexpected price supplement version")
    derived: dict[str, DerivedRule] = {}
    for item in data.get("derived", []):
        food_id = _text(item, "food_id")
        source = _text(item, "source_food_id")
        included = tuple(item.get("included_if_in_menu", []))
        if not FOOD.fullmatch(food_id) or not FOOD.fullmatch(source) or food_id == source or food_id in derived \
                or not all(isinstance(i, str) and FOOD.fullmatch(i) for i in included):
            raise ValueError(f"Invalid derived price rule: {food_id}")
        derived[food_id] = DerivedRule(food_id, _text(item, "name"), source, _text(item, "source_name"),
                                       _decimal(item.get("ratio"), "ratio", food_id), included, _text(item, "basis"),
                                       item.get("estimate", False) is True)
    estimates: dict[str, EstimateRule] = {}
    for item in data.get("estimates", []):
        food_id = _text(item, "food_id")
        url = _text(item, "source_url")
        if not FOOD.fullmatch(food_id) or food_id in estimates or not url.startswith("https://") \
                or item.get("package_unit") != "g" or not isinstance(item.get("whole_package", False), bool) \
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("observed", ""))):
            raise ValueError(f"Invalid price estimate rule: {food_id}")
        estimates[food_id] = EstimateRule(
            food_id, _text(item, "name"), _decimal(item.get("price"), "price", food_id),
            _decimal(item.get("package_quantity"), "package_quantity", food_id), "g",
            item.get("whole_package", False), item["observed"], _text(item, "source_name"), url,
            _text(item, "basis"))
    return PriceSupplements(derived, estimates)


@lru_cache(maxsize=4)
def _load(path: str) -> PriceSupplements:
    file = Path(path)
    if not file.is_file():
        return PriceSupplements({}, {})
    return parse_supplements(json.loads(file.read_text(encoding="utf-8")))


def load_supplements(path: str | Path = SUPPLEMENTS_PATH) -> PriceSupplements:
    return _load(str(path))
