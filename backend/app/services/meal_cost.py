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
from decimal import ROUND_CEILING, Decimal
from typing import Callable, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.meal_calc.types import IngredientRequirement
from app.services.synonyms import confirmed_equivalents
from app.source_adapters.price_collection import load_price_exempt
from app.source_adapters.price_models import PriceQuote, QuoteStatus

ZERO = Decimal(0)
QuoteLookup = Callable[[str], PriceQuote | None]

# 預算判定結果（spec 錯誤碼）
BUDGET_OK = "OK"
PRICE_INCOMPLETE = "PRICE_INCOMPLETE"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

# 未知價格原因（來源報價失敗時沿用其 missing_reason）
NO_PRICE_DATA = "NO_PRICE_DATA"
UNIT_MISMATCH = "UNIT_MISMATCH"
PRICE_EXPIRED = "PRICE_EXPIRED"
INVALID_PACKAGE = "INVALID_PACKAGE"


@dataclass(frozen=True)
class IngredientCost:
    """一項配方食材的缺料與價格。price_status：owned／exempt／known／unknown。"""

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
    budget: Decimal
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
        """前端價格區只列需補買的食材；已有種類與免計價（水）不列。"""
        return [line for line in self.lines if line.price_status in ("known", "unknown")]


def compute_meal_cost(
    requirements: Iterable[IngredientRequirement],
    available_food_ids: Iterable[str],
    budget: Decimal,
    quote_for: QuoteLookup,
    *,
    now: datetime,
    exempt_food_ids: set[str] | None = None,
) -> MealCost:
    """純計算：價格由 quote_for(food_id) 提供，便於不連資料庫測試。"""
    owned_ids = confirmed_equivalents(available_food_ids)
    exempt_ids = load_price_exempt() if exempt_food_ids is None else exempt_food_ids
    lines = []
    for item in requirements:
        if item.food_id in owned_ids:
            lines.append(_zero_line(item, "owned"))
        elif item.food_id in exempt_ids:
            lines.append(_zero_line(item, "exempt"))
        else:
            lines.append(_priced_line(item, quote_for(item.food_id), now))

    complete = all(line.cost is not None for line in lines)
    known_purchase = sum((line.purchase_price for line in lines if line.purchase_price is not None), ZERO)
    known_cost = sum((line.cost for line in lines if line.cost is not None), ZERO)
    if known_cost > budget:
        status = BUDGET_EXCEEDED  # 即使還有未知價格，已知成本已超預算就不能採用
    elif not complete:
        status = PRICE_INCOMPLETE
    else:
        status = BUDGET_OK
    return MealCost(lines, budget, known_purchase, known_cost, complete, status)


def _zero_line(item: IngredientRequirement, status: str) -> IngredientCost:
    shortage = ZERO if status == "owned" else item.grams
    return IngredientCost(item.food_id, item.grams, shortage, status, packs=0, purchase_price=ZERO, cost=ZERO)


def _priced_line(item: IngredientRequirement, quote: PriceQuote | None, now: datetime) -> IngredientCost:
    reason = _unusable_reason(quote, now)
    if reason is not None:
        return IngredientCost(item.food_id, item.grams, item.grams, "unknown", quote=quote, missing_reason=reason)
    ratio = item.grams / quote.package_quantity
    packs = int(ratio.to_integral_value(ROUND_CEILING))
    return IngredientCost(item.food_id, item.grams, item.grams, "known", quote=quote, packs=packs,
                          purchase_price=packs * quote.price, cost=ratio * quote.price)


def _unusable_reason(quote: PriceQuote | None, now: datetime) -> str | None:
    if quote is None:
        return NO_PRICE_DATA
    if quote.status is not QuoteStatus.SUCCESS or quote.price is None:
        return quote.missing_reason or NO_PRICE_DATA
    if quote.package_unit != "g":
        return UNIT_MISMATCH  # 配方以克計；容量包裝沒有可靠換算
    if not quote.package_quantity or quote.package_quantity <= 0:
        return INVALID_PACKAGE
    if quote.valid_until is not None and quote.valid_until < now:
        return PRICE_EXPIRED
    return None


# 每個來源（全聯、萬家福）各取最新一筆成功快照
LATEST_SUCCESS_PER_PROVIDER_SQL = """
    SELECT DISTINCT ON (provider) quote_payload FROM price_quote_snapshots
    WHERE status = 'success' AND quote_payload -> 'food_ids' ? :food_id
    ORDER BY provider, queried_at DESC, id DESC
"""
LATEST_ANY_SQL = """
    SELECT quote_payload FROM price_quote_snapshots
    WHERE quote_payload -> 'food_ids' ? :food_id
    ORDER BY queried_at DESC, id DESC
    LIMIT 1
"""
PRIMARY_RETAILER = "PX Mart"


def _unit_price(quote: PriceQuote) -> Decimal:
    return quote.price / quote.package_quantity if quote.package_quantity else Decimal("Infinity")


def latest_quote(db: Session, food_id: str) -> PriceQuote | None:
    """某 FOOD 編號最新的可用報價（唯讀查 T02 快照，含第二來源萬家福）。

    每個來源取最新一筆成功快照（保留原查詢時間，不冒充新取得）。兩個來源都有可用價時，
    T02 沒有跨來源規則，因此取「完整包裝（克）且單位價最低」者，同價優先全聯，理由寫入
    selection_reasons。從未成功則回最新一筆失敗快照，保留來源的缺值原因（估價不算已知價格）。
    完全沒查過回 None。
    """
    params = {"food_id": food_id}
    successes = [PriceQuote.model_validate(p) for p in
                 db.execute(text(LATEST_SUCCESS_PER_PROVIDER_SQL), params).scalars().all()]
    if successes:
        ranked = sorted(successes, key=lambda q: (q.package_unit != "g", _unit_price(q),
                                                  q.retailer != PRIMARY_RETAILER))
        chosen = ranked[0]
        if len(ranked) == 1:
            return chosen
        others = ", ".join(f"{q.retailer} {q.price}/{q.package_quantity}{q.package_unit}" for q in ranked[1:])
        reason = (f"cross-source: {chosen.retailer} {chosen.price}/{chosen.package_quantity}"
                  f"{chosen.package_unit} has the lowest unit price for a complete package (vs {others})")
        return chosen.model_copy(update={"selection_reasons": [*chosen.selection_reasons, reason]})
    payload = db.execute(text(LATEST_ANY_SQL), params).scalars().first()
    return PriceQuote.model_validate(payload) if payload is not None else None


def meal_cost_from_db(
    db: Session,
    requirements: Iterable[IngredientRequirement],
    available_food_ids: Iterable[str],
    budget: Decimal,
    *,
    now: datetime,
) -> MealCost:
    return compute_meal_cost(requirements, available_food_ids, budget, lambda food_id: latest_quote(db, food_id),
                             now=now)
