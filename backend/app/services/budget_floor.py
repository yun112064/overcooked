"""問卷預算下限：預算低於估算的最低合理金額時不能送出生成（2026-10-28 使用者決定）。

估算的是「每道菜每人份最少要花多少」的合理價位，只擋明顯不夠的預算：
- 每道合格食譜每 1n 成本 = Σ 需補買食材的原食譜每 1n 克數 × 單位價（已有種類、調味料、免計價的水
  為 0 元，主食不計價，同 T13）；只用克數與需補買食材價格都齊全的食譜，缺價食譜不估（不把缺價當 0 元）；
- 點心不進正餐；有選主食時主食類食譜也不算（同 T11）；同一道菜（standard_dish_id）取最便宜的做法；
- 每道菜最低合理價 = 上述每道菜成本的第 REFERENCE_PERCENTILE 百分位。不取最小值：資料裡有用量
  錯誤（例如油菜只有 1 克）或分類不當（冬瓜茶磚）的食譜，最小值只有幾毛錢，等於不擋；
  demo 資料約每道 5 元，過去實際生成結果每道每人份最低約 8.6 元，正常預算不會被擋；
- 可估價的菜不足 MIN_PRICED_DISHES 道時資料不夠，不估（不擋）；
- 下限 = 每道菜最低合理價 × 菜數 × 本餐合計 n，無條件進位到整數元。

食譜每 1n 用量與單位價各自快取 CACHE_SECONDS 秒：問卷每次讀取都要算，不能每次查全部報價。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

CACHE_SECONDS = 300
REFERENCE_PERCENTILE = 5
MIN_PRICED_DISHES = 20
ZERO = Decimal(0)


@dataclass(frozen=True)
class RecipeFloor:
    """一道食譜每 1n 的原用量：((food_id, 克數), ...)；克數未知的食材 known_grams 為 False。"""

    recipe_id: str
    standard_dish_id: str
    category: str | None
    grams: tuple[tuple[str, Decimal], ...]
    known_grams: bool = True


_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, load):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = load()
    _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _recipe_floors(db: Session) -> list[RecipeFloor]:
    from app.estimation.store import load_snapshot
    from app.models import Recipe
    from app.services.meal_calculation import load_recipe_lines
    from app.services.recipe_import import standard_dish_id

    factors = load_snapshot(db)
    floors = []
    for recipe_id, name, category in db.execute(select(Recipe.recipe_id, Recipe.recipe_name, Recipe.category)
                                                .where(Recipe.is_eligible.is_(True))).all():
        lines = load_recipe_lines(db, recipe_id, factors)
        grams = tuple((ln.food_id, ln.grams_per_n) for ln in lines if ln.grams_per_n is not None)
        known = all(ln.grams_per_n is not None for ln in lines)
        # 與 T11 相同依菜名重算標準菜色 ID（異體字同一道菜）
        floors.append(RecipeFloor(recipe_id, standard_dish_id(name), category, grams, known))
    return floors


def _unit_prices(db: Session, now: datetime) -> dict[str, Decimal]:
    """每克價格；沒有可用報價的食材不列（用到它的食譜不估價）。"""
    from app.services.meal_cost import _unusable_reason, latest_success_quotes

    return {food_id: quote.price / quote.package_quantity
            for food_id, quote in latest_success_quotes(db, now=now).items()
            if _unusable_reason(quote, now) is None}


def reference_dish_cost(floors: list[RecipeFloor], unit_prices: dict[str, Decimal], free_food_ids: set[str],
                        *, has_staple: bool) -> Decimal | None:
    """每道菜每 1n 的最低合理價（可估價菜色成本的第 REFERENCE_PERCENTILE 百分位）；資料不足回 None。"""
    from app.services.recipe_retrieval import DESSERT, STAPLE

    skipped = {DESSERT, STAPLE} if has_staple else {DESSERT}
    per_dish: dict[str, Decimal] = {}
    for floor in floors:
        if floor.category in skipped or not floor.known_grams:
            continue
        paid = [(f, g) for f, g in floor.grams if f not in free_food_ids]
        if any(f not in unit_prices for f, _ in paid):
            continue  # 缺價：不估
        cost = sum((g * unit_prices[f] for f, g in paid), ZERO)
        if floor.standard_dish_id not in per_dish or cost < per_dish[floor.standard_dish_id]:
            per_dish[floor.standard_dish_id] = cost
    if len(per_dish) < MIN_PRICED_DISHES:
        return None
    costs = sorted(per_dish.values())
    return costs[(len(costs) - 1) * REFERENCE_PERCENTILE // 100]


def minimum_budget(db: Session, *, dish_count: int, total_n: Decimal, available_food_ids: Iterable[str],
                   has_staple: bool, now: datetime | None = None) -> Decimal | None:
    """本餐預算下限（整數元）；無法估算時回 None（不擋）。"""
    from app.services.seasonings import load_seasonings
    from app.services.synonyms import confirmed_equivalents
    from app.source_adapters.price_collection import load_price_exempt

    now = now or datetime.now(timezone.utc)
    floors = _cached("floors", lambda: _recipe_floors(db))
    prices = _cached("prices", lambda: _unit_prices(db, now))
    free = set(confirmed_equivalents(list(available_food_ids))) | load_seasonings() | load_price_exempt()
    per_dish = reference_dish_cost(floors, prices, free, has_staple=has_staple)
    if per_dish is None or per_dish <= 0:
        return None
    return (per_dish * dish_count * total_n).to_integral_value(ROUND_CEILING)
