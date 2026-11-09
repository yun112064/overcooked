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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.meal_calc.types import IngredientRequirement
from app.services.seasonings import load_seasonings
from app.services.synonyms import confirmed_equivalents
from app.source_adapters.price_collection import load_price_exempt
from app.source_adapters.price_models import PriceQuote, QuoteStatus
from app.source_adapters.price_scope import latest_quote as latest_quote
from app.source_adapters.price_scope import latest_success_quotes as latest_success_quotes
from app.source_adapters.price_scope import (LATEST_QUOTES_SQL, LATEST_SUCCESS_ALL_SQL, PRIMARY_RETAILER,
                                             _choose, latest_quotes, materialize_quote, quote_unusable_reason)

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
    required_grams: Decimal | None
    shortage_grams: Decimal | None
    price_status: str
    quote: PriceQuote | None = None
    packs: int | None = None
    purchase_price: Decimal | None = None
    cost: Decimal | None = None
    missing_reason: str | None = None
    required_ml: Fraction | None = None
    shortage_ml: Fraction | None = None
    exact_cost: Fraction = Fraction()

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
    unquantified_food_ids: tuple[str, ...] = ()

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
    free = _free_foods(available_food_ids, exempt_food_ids, seasoning_food_ids)
    return _compute_meal_cost(requirements, free, budget, lambda item: quote_for(item.food_id), now)


def _free_foods(
    available_food_ids: Iterable[str],
    exempt_food_ids: set[str] | None,
    seasoning_food_ids: set[str] | None,
) -> dict[str, str]:
    exempt = load_price_exempt() if exempt_food_ids is None else exempt_food_ids
    seasonings = load_seasonings() if seasoning_food_ids is None else seasoning_food_ids
    return ({food_id: "exempt" for food_id in exempt}
            | {food_id: "seasoning" for food_id in seasonings}
            | {food_id: "owned" for food_id in confirmed_equivalents(available_food_ids)})


def _compute_meal_cost(
    requirements: Iterable[IngredientRequirement],
    free: dict[str, str],
    budget: Decimal | None,
    quote_for: Callable[[IngredientRequirement], PriceQuote | None],
    now: datetime,
    unquantified_food_ids: Iterable[str] = (),
) -> MealCost:
    lines = []
    for item in requirements:
        status = free.get(item.food_id)
        if status is not None:
            lines.append(_zero_line(item, status))
        else:
            lines.append(price_requirement(item, quote_for(item), now))

    unquantified = tuple(sorted(set(unquantified_food_ids) - free.keys()))
    complete = not unquantified and all(line.cost is not None for line in lines)
    known_purchase = sum((line.purchase_price for line in lines if line.purchase_price is not None), ZERO)
    known_cost = sum((line.cost for line in lines if line.cost is not None), ZERO)
    if budget is not None and sum((line.exact_cost for line in lines), Fraction()) > Fraction(budget):
        status = BUDGET_EXCEEDED  # 即使還有未知價格，已知成本已超預算就不能採用
    elif not complete:
        status = PRICE_INCOMPLETE
    else:
        status = BUDGET_OK
    return MealCost(lines, budget, known_purchase, known_cost, complete, status, unquantified)


def _zero_line(item: IngredientRequirement, status: str) -> IngredientCost:
    covered = status in ("owned", "seasoning")
    shortage = ZERO if covered and item.grams is not None else item.grams
    shortage_ml = Fraction() if covered and item.volume_ml is not None else item.volume_ml
    return IngredientCost(item.food_id, item.grams, shortage, status, packs=0, purchase_price=ZERO, cost=ZERO,
                          required_ml=item.volume_ml, shortage_ml=shortage_ml)


def _volume_multiplier(quote: PriceQuote | None) -> int | None:
    unit = (quote.package_unit or "").casefold() if quote else ""
    return {"ml": 1, "毫升": 1, "l": 1000, "公升": 1000}.get(unit)


def _volume_quote_reason(quote: PriceQuote, now: datetime) -> str | None:
    if quote.status is not QuoteStatus.SUCCESS or quote.price is None:
        return quote.missing_reason or "NO_PRICE_DATA"
    if quote.package_quantity is None or quote.package_quantity <= 0:
        return "INVALID_PACKAGE"
    if quote.valid_from is not None and quote.valid_from > now:
        return "PRICE_NOT_YET_VALID"
    if quote.valid_until is not None and quote.valid_until < now:
        return "PRICE_EXPIRED"
    if quote.currency != "TWD":
        return "CURRENCY_MISMATCH"
    return None


def price_requirement(item: IngredientRequirement, quote: PriceQuote | None, now: datetime) -> IngredientCost:
    multiplier = _volume_multiplier(quote)
    by_volume = item.volume_ml is not None and multiplier is not None
    reason = _volume_quote_reason(quote, now) if by_volume else quote_unusable_reason(quote, now)
    if reason is None and item.volume_ml is not None and quote.valid_from is not None and quote.valid_from > now:
        reason = "PRICE_NOT_YET_VALID"
    if reason is None and not by_volume and item.grams is None:
        reason = "UNIT_MISMATCH"
    if reason is not None:
        return IngredientCost(item.food_id, item.grams, item.grams if item.volume_ml is None else None,
                              "unknown", quote=quote, missing_reason=reason,
                              required_ml=item.volume_ml, shortage_ml=item.volume_ml)
    quantity = Fraction(item.volume_ml if by_volume else item.grams)
    package_quantity = Fraction(quote.package_quantity) * (multiplier if by_volume else 1)
    ratio = quantity / package_quantity
    packs = ceil(ratio)
    exact_cost = ratio * Fraction(quote.price)
    return IngredientCost(item.food_id, item.grams, None if by_volume else item.grams, "known", quote=quote,
                          packs=packs, purchase_price=packs * quote.price,
                          cost=Decimal(exact_cost.numerator) / Decimal(exact_cost.denominator),
                          required_ml=item.volume_ml, shortage_ml=item.volume_ml if by_volume else None,
                          exact_cost=exact_cost)


def cost_quote_candidates(db: Session, food_ids: Iterable[str] | None = None) -> dict[str, list[PriceQuote]]:
    """Reuse the T02 snapshot scope. Keep the original volume alongside a verified mass projection."""
    if food_ids is None:
        rows = db.execute(text(LATEST_SUCCESS_ALL_SQL)).mappings()
    else:
        ids = sorted(set(food_ids))
        if not ids:
            return {}
        rows = db.execute(LATEST_QUOTES_SQL, {"food_ids": ids}).mappings()
    grouped: dict[str, list[PriceQuote]] = {}
    for row in rows:
        quote = materialize_quote(row)
        candidates = grouped.setdefault(row["food_id"], [])
        candidates.append(quote)
        if row.get("net_mass_g") is not None and quote.weight_basis == "manufacturer_net_mass":
            candidates.append(materialize_quote({**row, "net_mass_g": None}))
    return grouped


def select_cost_quote(item: IngredientRequirement, quotes: list[PriceQuote], now: datetime) -> PriceQuote | None:
    volumes = [q for q in quotes if _volume_multiplier(q) is not None]
    if item.volume_ml is not None:
        usable = [q for q in volumes if _volume_quote_reason(q, now) is None]
        if usable:
            return min(usable, key=lambda q: (Fraction(q.price) / (Fraction(q.package_quantity) * _volume_multiplier(q)),
                                              q.retailer != PRIMARY_RETAILER))
        if item.grams is None and volumes:
            return max(volumes, key=lambda q: q.queried_at)
    successes = [q for q in quotes if q.status is QuoteStatus.SUCCESS]
    if item.volume_ml is not None:
        successes = [q for q in successes if q.valid_from is None or q.valid_from <= now]
    if successes:
        return _choose(successes, now)
    return max(quotes, key=lambda q: q.queried_at, default=None)


def meal_cost_from_db(
    db: Session,
    requirements: Iterable[IngredientRequirement],
    available_food_ids: Iterable[str],
    budget: Decimal | None,
    *,
    now: datetime,
    unquantified_food_ids: Iterable[str] = (),
) -> MealCost:
    items = list(requirements)
    free = _free_foods(available_food_ids, None, None)
    paid = [item for item in items if item.food_id not in free]
    ids = [item.food_id for item in paid]
    if any(item.volume_ml is not None for item in paid):
        candidates = cost_quote_candidates(db, ids)
        return _compute_meal_cost(items, free, budget,
                                  lambda item: select_cost_quote(item, candidates.get(item.food_id, []), now),
                                  now, unquantified_food_ids)
    quotes = latest_quotes(db, ids, now=now)
    return _compute_meal_cost(items, free, budget, lambda item: quotes.get(item.food_id), now, unquantified_food_ids)
