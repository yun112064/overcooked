"""問卷預算下限：預算低於估算的最低合理金額時不能送出生成（2026-10-28 使用者決定）。

估算的是「每道菜每人份最少要花多少」的合理價位，只擋明顯不夠的預算：
- 每道合格食譜每 1n 成本 = Σ 需補買食材的比例成本（已有種類、調味料、免計價的水為 0 元，同 T13）；
  只用需補買食材的用量與同單位報價都齊全的食譜；
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
from decimal import Decimal
from fractions import Fraction
from math import ceil
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.management.active_price_policy import active_overrides
from app.meal_calc.types import IngredientRequirement
from app.services.meal_cost import cost_quote_candidates, price_requirement, select_cost_quote
from app.source_adapters.price_models import PriceQuote

CACHE_SECONDS = 300
REFERENCE_PERCENTILE = 5
MIN_PRICED_DISHES = 20


@dataclass(frozen=True)
class RecipeFloor:
    """一道食譜每 1n 可用於 T13 的原用量，以及缺少用量的 Food_ID。"""

    recipe_id: str
    standard_dish_id: str
    category: str | None
    requirements: list[IngredientRequirement]
    unquantified_food_ids: frozenset[str] = frozenset()


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


def _unit_prices(db: Session, now: datetime, food_ids=None) -> dict[str, Decimal]:
    """Compatibility view for gram-only callers; volume prices never become gram prices."""
    from app.source_adapters.price_scope import latest_success_quotes, quote_unusable_reason

    quotes = latest_success_quotes(db, now=now)
    if settings.management_price_policy_enabled:
        for food_id, decision in active_overrides(
            db, food_ids or set(quotes), quotes, as_of=now,
            recipe_release_id=settings.generation_recipe_release_id or None,
        ).items():
            if decision.quote is not None:
                quotes[food_id] = decision.quote
            else:
                quotes.pop(food_id, None)
    return {food_id: quote.price / quote.package_quantity for food_id, quote in quotes.items()
            if quote_unusable_reason(quote, now) is None}


def _recipe_floors(db: Session) -> list[RecipeFloor]:
    from app.estimation.store import load_snapshot
    from app.models import Recipe
    from app.services.meal_calculation import load_recipe_lines, pricing_requirements
    from app.services.recipe_import import standard_dish_id

    factors = load_snapshot(db)
    floors = []
    for recipe_id, name, category in db.execute(select(Recipe.recipe_id, Recipe.recipe_name, Recipe.category)
                                                .where(Recipe.is_eligible.is_(True))).all():
        lines = load_recipe_lines(db, recipe_id, factors)
        requirements = pricing_requirements(lines, (ln.grams_per_n for ln in lines), Decimal(1))
        unquantified = frozenset(ln.food_id for ln in lines) - {item.food_id for item in requirements}
        # 與 T11 相同依菜名重算標準菜色 ID（異體字同一道菜）
        floors.append(RecipeFloor(recipe_id, standard_dish_id(name), category, requirements, unquantified))
    return floors


def reference_dish_cost(floors: list[RecipeFloor], quotes: dict[str, list[PriceQuote]], free_food_ids: set[str],
                        *, has_staple: bool, now: datetime) -> Fraction | None:
    """每道菜每 1n 的最低合理價（可估價菜色成本的第 REFERENCE_PERCENTILE 百分位）；資料不足回 None。"""
    from app.services.recipe_retrieval import DESSERT, STAPLE

    skipped = {DESSERT, STAPLE} if has_staple else {DESSERT}
    per_dish: dict[str, Fraction] = {}
    for floor in floors:
        if floor.category in skipped or floor.unquantified_food_ids - free_food_ids:
            continue
        priced = [price_requirement(item, select_cost_quote(item, quotes.get(item.food_id, []), now), now)
                  for item in floor.requirements if item.food_id not in free_food_ids]
        if any(line.cost is None for line in priced):
            continue  # 缺價：不估
        cost = sum((line.exact_cost for line in priced), Fraction())
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
    prices = _cached("prices", lambda: cost_quote_candidates(db))
    if settings.management_price_policy_enabled:
        requirements = [item for floor in floors for item in floor.requirements]
        # Re-read the active binding every time; a new publication immediately takes effect.
        managed = {}
        for volume in {item.volume_ml is not None for item in requirements}:
            fallback = {item.food_id: select_cost_quote(item, prices.get(item.food_id, []), now)
                        for item in requirements if (item.volume_ml is not None) == volume}
            decisions = active_overrides(db, set(fallback), fallback, as_of=now,
                                         recipe_release_id=settings.generation_recipe_release_id or None,
                                         allow_volume=volume)
            for food_id, decision in decisions.items():
                candidates = managed.setdefault(food_id, [])
                if decision.quote is not None:
                    candidates.append(decision.quote)
        prices = dict(prices)
        prices.update(managed)
    free = set(confirmed_equivalents(list(available_food_ids))) | load_seasonings() | load_price_exempt()
    per_dish = reference_dish_cost(floors, prices, free, has_staple=has_staple, now=now)
    if per_dish is None or per_dish <= 0:
        return None
    return Decimal(ceil(per_dish * dish_count * Fraction(total_n)))
