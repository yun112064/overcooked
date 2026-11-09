"""T13：缺少種類、完整採購價與比例成本（R08）。

已有種類一律足量：缺量 0、不查價、成本貢獻 0。未列為已有的種類，缺量即整餐需求量
（不追蹤庫存重量）。

    需購包數 = ceil(缺量 / 每包量)
    採購價格 = 需購包數 × 單包售價
    成本價   = 缺量 / 每包量 × 單包售價   ← 只有這個參與預算判定

全程 Decimal、不取整；顯示時才取整。查無可用價格時成本為「未知」（None＋原因），絕不填 0。
估價（review_required 的 estimated_price）不算已知價格。採購整包的餘料不算食用量：
本模組只回傳金額，營養一律由呼叫端按配方需求量（required_grams）計算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil
from fractions import Fraction
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from app.meal_calc.types import IngredientRequirement
from app.services.seasonings import load_seasonings
from app.services.synonyms import confirmed_equivalents
from app.source_adapters.price_collection import load_price_exempt
from app.source_adapters.price_models import PriceQuote
from app.source_adapters.price_scope import latest_quote as latest_quote
from app.source_adapters.price_scope import latest_success_quotes as latest_success_quotes
from app.source_adapters.price_scope import latest_quotes, quote_unusable_reason

ZERO = Decimal(0)
QuoteLookup = Callable[[str], PriceQuote | None]

# 預算判定結果（spec 錯誤碼）
BUDGET_OK = "OK"
PRICE_INCOMPLETE = "PRICE_INCOMPLETE"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

@dataclass(frozen=True)
class IngredientCost:
    """一項配方食材的缺料與價格。price_status：owned／seasoning／exempt／known／unknown。"""

    food_id: str
    required_grams: Decimal
    shortage_grams: Decimal
    price_status: str
    quote: PriceQuote | None = None
    packs: int | None = None
    purchase_price: Decimal | None = None
    cost: Decimal | None = None
    missing_reason: str | None = None

    @property
    def owned(self) -> bool:
        return self.price_status == "owned"


@dataclass(frozen=True)
class MealCost:
    lines: list[IngredientCost]
    budget: Decimal | None
    known_purchase_total: Decimal
    known_cost_total: Decimal
    price_complete: bool
    budget_status: str

    @property
    def purchase_total(self) -> Decimal | None:
        """完整採購價總和；有未知價格時為 None（不是 0）。"""
        return self.known_purchase_total if self.price_complete else None

    @property
    def cost_total(self) -> Decimal | None:
        """比例成本總和；有未知價格時為 None（不是 0）。"""
        return self.known_cost_total if self.price_complete else None

    @property
    def missing_items(self) -> list[IngredientCost]:
        """前端價格區只列需補買的食材；已有種類、調味料與免計價（水）不列。"""
        return [line for line in self.lines if line.price_status in ("known", "unknown")]


def compute_meal_cost(
    requirements: Iterable[IngredientRequirement],
    available_food_ids: Iterable[str],
    budget: Decimal | None,
    quote_for: QuoteLookup,
    *,
    now: datetime,
    exempt_food_ids: set[str] | None = None,
    seasoning_food_ids: set[str] | None = None,
) -> MealCost:
    """純計算：價格由 quote_for(food_id) 提供，便於不連資料庫測試。

    調味料（決策 #44，config/seasoning_ingredients.json）比照已有種類：足量、缺量 0、不查價、成本 0，
    狀態標為 seasoning（不列入缺料／價格區）；營養仍由呼叫端按配方需求量計算。
    """
    free = free_foods(available_food_ids, exempt_food_ids, seasoning_food_ids)
    return _compute_meal_cost(requirements, free, budget, quote_for, now)


def free_foods(
    available_food_ids: Iterable[str],
    exempt_food_ids: set[str] | None = None,
    seasoning_food_ids: set[str] | None = None,
) -> dict[str, str]:
    exempt = load_price_exempt() if exempt_food_ids is None else exempt_food_ids
    seasonings = load_seasonings() if seasoning_food_ids is None else seasoning_food_ids
    return ({food_id: "exempt" for food_id in exempt}
            | {food_id: "seasoning" for food_id in seasonings}
            | {food_id: "owned" for food_id in confirmed_equivalents(available_food_ids)})


def _compute_meal_cost(
    requirements: Iterable[IngredientRequirement],
    free: dict[str, str],
    budget: Decimal,
    quote_for: QuoteLookup,
    now: datetime,
) -> MealCost:
    lines = []
    for item in requirements:
        status = free.get(item.food_id)
        if status is not None:
            lines.append(_zero_line(item, status))
        else:
            lines.append(_priced_line(item, quote_for(item.food_id), now))

    complete = all(line.cost is not None for line in lines)
    known_purchase = sum((line.purchase_price for line in lines if line.purchase_price is not None), ZERO)
    known_cost = sum((line.cost for line in lines if line.cost is not None), ZERO)
    if budget is not None and sum(map(_exact_cost, lines), Fraction()) > Fraction(budget):
        status = BUDGET_EXCEEDED  # 即使還有未知價格，已知成本已超預算就不能採用
    elif not complete:
        status = PRICE_INCOMPLETE
    else:
        status = BUDGET_OK
    return MealCost(lines, budget, known_purchase, known_cost, complete, status)


def _exact_cost(line: IngredientCost) -> Fraction:
    """預算判定用精確值：循環小數經 Decimal 捨入後可能略高或略低於預算。"""
    if line.price_status != "known":
        return Fraction()
    return Fraction(line.shortage_grams) * Fraction(line.quote.price) / Fraction(line.quote.package_quantity)


def _zero_line(item: IngredientRequirement, status: str) -> IngredientCost:
    shortage = ZERO if status in ("owned", "seasoning") else item.grams
    return IngredientCost(item.food_id, item.grams, shortage, status, packs=0, purchase_price=ZERO, cost=ZERO)


def _priced_line(item: IngredientRequirement, quote: PriceQuote | None, now: datetime) -> IngredientCost:
    reason = quote_unusable_reason(quote, now)
    if reason is not None:
        return IngredientCost(item.food_id, item.grams, item.grams, "unknown", quote=quote, missing_reason=reason)
    packs = ceil(Fraction(item.grams) / Fraction(quote.package_quantity))
    return IngredientCost(item.food_id, item.grams, item.grams, "known", quote=quote, packs=packs,
                          purchase_price=packs * quote.price, cost=item.grams * quote.price / quote.package_quantity)


def meal_cost_from_db(
    db: Session,
    requirements: Iterable[IngredientRequirement],
    available_food_ids: Iterable[str],
    budget: Decimal | None,
    *,
    now: datetime,
) -> MealCost:
    items = list(requirements)
    free = free_foods(available_food_ids)
    quotes = latest_quotes(db, (item.food_id for item in items if item.food_id not in free), now=now)
    return _compute_meal_cost(items, free, budget, quotes.get, now)
