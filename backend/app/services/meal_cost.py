"""T13：缺少種類、完整採購價與比例成本（R08）。

已有種類一律足量：缺量 0、不查價、成本貢獻 0。未列為已有的種類，缺量即整餐需求量
（不追蹤庫存重量）。

    需購包數 = ceil(缺量 / 每包量)
    採購價格 = 需購包數 × 單包售價
    成本價   = 缺量 / 每包量 × 單包售價   ← 只有這個參與預算判定

全程 Decimal、不取整；顯示時才取整。查無可用價格時成本為「未知」（None＋原因），絕不填 0。
估價（2026-11-16 使用者決定）：沒有可用的商店報價時，依 config/t02_price_supplements.json 以其他食材的
即時報價換算（derived），或用有出處與日期的估價（estimate；使用者確認商品的行情估價也算），狀態為
estimated：計入成本與預算，但一律標示為估價，不當作當日售價。採購整包的餘料不算食用量：
本模組只回傳金額，營養一律由呼叫端按配方需求量（required_grams）計算。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from math import ceil
from fractions import Fraction
from typing import Callable, Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.management.active_price_policy import active_overrides
from app.meal_calc.types import IngredientRequirement
from app.services.seasonings import load_seasonings
from app.services.synonyms import confirmed_equivalents
from app.source_adapters.price_collection import load_price_exempt
from app.source_adapters.price_models import PriceQuote, QuoteStatus, SelectionTier
from app.source_adapters.price_scope import latest_quote as latest_quote
from app.source_adapters.price_scope import latest_success_quotes as latest_success_quotes
from app.source_adapters.price_scope import (LATEST_QUOTES_SQL, LATEST_SUCCESS_ALL_SQL, PRIMARY_RETAILER, SCOPED_QUOTES_SQL,
                                             _choose, latest_quotes, materialize_quote, quote_unusable_reason)

ZERO = Decimal(0)
QuoteLookup = Callable[[str], PriceQuote | None]

# 預算判定結果（spec 錯誤碼）
BUDGET_OK = "OK"
PRICE_INCOMPLETE = "PRICE_INCOMPLETE"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

@dataclass(frozen=True)
class IngredientCost:
    """一項配方食材的缺料與價格。

    price_status：owned／seasoning／exempt／known／estimated／included／unknown。
    estimated 有金額但是估價；included 是已含在本餐另一項食材裡（例如泡乾香菇的水），成本 0。
    price_basis：derived（由 derived_from_food_id 的報價換算）／estimate／market_estimate／historical（過期的最後一筆售價）／included。
    """

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
    whole_package: bool = False
    price_basis: str | None = None
    derived_from_food_id: str | None = None
    estimate_note: str | None = None
    estimate_url: str | None = None

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
        if not self.price_complete or any(line.purchase_price is None for line in self.lines):
            return None
        return self.known_purchase_total

    @property
    def cost_total(self) -> Decimal | None:
        """比例成本總和；有未知價格時為 None（不是 0）。"""
        return self.known_cost_total if self.price_complete else None

    @property
    def missing_items(self) -> list[IngredientCost]:
        """前端價格區只列需補買的食材；已有種類、調味料與免計價（水）不列。"""
        return [line for line in self.lines if line.price_status in ("known", "estimated", "unknown")]

    @property
    def estimated_food_ids(self) -> tuple[str, ...]:
        return tuple(sorted({line.food_id for line in self.lines if line.price_status == "estimated"}))


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
    return _compute_meal_cost(requirements, free, budget, lambda item: quote_for(item.food_id), now)


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
    budget: Decimal | None,
    quote_for: Callable[[IngredientRequirement], PriceQuote | None],
    now: datetime,
    unquantified_food_ids: Iterable[str] = (),
    missing_reasons: dict[IngredientRequirement, str] | None = None,
    prepared: dict[IngredientRequirement, IngredientCost] | None = None,
) -> MealCost:
    lines = []
    for item in requirements:
        status = free.get(item.food_id)
        if status is not None:
            lines.append(_zero_line(item, status))
        elif prepared and item in prepared:
            lines.append(prepared[item])
        else:
            lines.append(price_requirement(item, quote_for(item), now, (missing_reasons or {}).get(item)))

    lines = _share_packages(_combine_packages(lines))

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


def _combine_packages(lines: list[IngredientCost]) -> list[IngredientCost]:
    """Round once for compatible demands using the exact same scoped product."""
    result: list[IngredientCost] = []
    groups: dict[tuple, int] = {}
    for line in lines:
        if line.price_status != "known":
            result.append(line)
            continue
        key = (line.food_id, line.quote.model_dump_json())
        if key not in groups:
            groups[key] = len(result)
            result.append(line)
            continue
        index = groups[key]
        prior = result[index]
        amounts = {}
        for field in ("required_grams", "shortage_grams", "required_ml", "shortage_ml"):
            left, right = getattr(prior, field), getattr(line, field)
            amounts[field] = left + right if left is not None and right is not None else None
        exact_cost = prior.exact_cost + line.exact_cost
        packs = ceil(exact_cost / Fraction(line.quote.price))
        result[index] = replace(prior, **amounts, exact_cost=exact_cost, packs=packs,
                                purchase_price=packs * line.quote.price,
                                cost=Decimal(exact_cost.numerator) / Decimal(exact_cost.denominator))
    # A mass projection and its source volume identify the same physical package,
    # but the source shortages must retain their different units. Until the result
    # contract supports shared purchase groups, do not charge two rounded packs.
    package_units: dict[tuple, set[str]] = {}
    package_keys = []
    for line in result:
        quote = line.quote
        key = None
        if line.price_status == "known" and quote.snapshot_id is not None:
            key = (line.food_id, quote.retailer, quote.snapshot_id, quote.product_id,
                   quote.product_name, quote.price, quote.recipe_release_id, quote.price_data_release_id)
            package_units.setdefault(key, set()).add(quote.package_unit)
        package_keys.append(key)
    return [replace(line, packs=None, purchase_price=None,
                    missing_reason="PACKAGE_DEMAND_UNITS_REQUIRE_RECONCILIATION")
            if key is not None and len(package_units[key]) > 1 else line
            for line, key in zip(result, package_keys)]


def _share_packages(lines: list[IngredientCost]) -> list[IngredientCost]:
    """Round once for demands that buy the same package but are separate lines.

    - a derived food and its source food on one quote (水煮蛋 and 雞蛋 on the same egg pack);
    - one food split into several requirements (grams-only and grams+mL) priced by the same estimate.
    The first line of a group carries the packs and purchase price; the others keep their proportional cost
    and show 0 packs. A whole-package estimate is charged once per food: later lines cost 0.
    """
    groups: dict[tuple, list[int]] = {}
    for index, line in enumerate(lines):
        if line.packs is None or line.purchase_price is None or not line.packs:
            continue
        if line.price_status == "known" and line.quote is not None and line.quote.price:
            key = ("known", line.derived_from_food_id or line.food_id, line.quote.model_dump_json())
        elif line.price_status == "estimated":
            key = ("estimated", line.food_id, line.price_basis, line.estimate_note,
                   line.quote.model_dump_json() if line.quote is not None else None)
        else:
            continue
        groups.setdefault(key, []).append(index)
    result = list(lines)
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        first = result[indexes[0]]
        package_price = first.purchase_price / first.packs
        if first.whole_package:
            for index in indexes[1:]:
                line = result[index]
                result[index] = replace(line, packs=0, purchase_price=ZERO, cost=ZERO, exact_cost=Fraction())
            continue
        total = sum((result[index].exact_cost for index in indexes), Fraction())
        packs = ceil(total / Fraction(package_price))
        result[indexes[0]] = replace(first, packs=packs, purchase_price=packs * package_price)
        for index in indexes[1:]:
            result[index] = replace(result[index], packs=0, purchase_price=ZERO)
    return result


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
    return quote_unusable_reason(quote, now, allow_volume=True)


def price_requirement(item: IngredientRequirement, quote: PriceQuote | None, now: datetime,
                      missing_reason: str | None = None) -> IngredientCost:
    multiplier = _volume_multiplier(quote)
    by_volume = item.volume_ml is not None and multiplier is not None
    reason = missing_reason or (_volume_quote_reason(quote, now) if by_volume else quote_unusable_reason(quote, now))
    if reason is None and item.volume_ml is not None and quote.valid_from is not None and quote.valid_from > now:
        reason = "PRICE_NOT_YET_VALID"
    if reason is None and not by_volume and item.grams is None:
        reason = "UNIT_MISMATCH"
    if reason is not None:
        return IngredientCost(item.food_id, item.grams, item.grams,
                              "unknown", quote=quote, missing_reason=reason,
                              required_ml=item.volume_ml, shortage_ml=item.volume_ml)
    quantity = Fraction(item.volume_ml if by_volume else item.grams)
    package_quantity = Fraction(quote.package_quantity) * (multiplier if by_volume else 1)
    ratio = quantity / package_quantity
    packs = ceil(ratio)
    exact_cost = ratio * Fraction(quote.price)
    return IngredientCost(item.food_id, item.grams, item.grams, "known", quote=quote,
                          packs=packs, purchase_price=packs * quote.price,
                          cost=Decimal(exact_cost.numerator) / Decimal(exact_cost.denominator),
                          required_ml=item.volume_ml, shortage_ml=item.volume_ml,
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
    recipe_release_id: str | None = None,
) -> MealCost:
    items = list(requirements)
    free = free_foods(available_food_ids)
    paid = [item for item in items if item.food_id not in free]
    ids = [item.food_id for item in paid]
    allow_volume = any(item.volume_ml is not None for item in paid)
    if allow_volume:
        candidates = cost_quote_candidates(db, ids)
        selected = {item: select_cost_quote(item, candidates.get(item.food_id, []), now) for item in paid}
    else:
        quotes = latest_quotes(db, ids, now=now)
        selected = {item: quotes.get(item.food_id) for item in paid}
    reasons = {}
    release = settings.generation_recipe_release_id or None if recipe_release_id is None else recipe_release_id
    if settings.management_price_policy_enabled:
        for volume in {item.volume_ml is not None for item in paid}:
            dimension_items = [item for item in paid if (item.volume_ml is not None) == volume]
            fallback = {item.food_id: selected[item] for item in dimension_items}
            overrides = active_overrides(db, set(fallback), fallback, as_of=now,
                                         recipe_release_id=release, allow_volume=volume)
            for item in dimension_items:
                if item.food_id in overrides:
                    decision = overrides[item.food_id]
                    selected[item] = decision.quote
                    if decision.reason:
                        reasons[item] = decision.reason
    open_items = [item for item in paid if item not in reasons]
    _price_by_confirmed_synonym(db, open_items, selected, now, release)
    prepared = _price_by_supplements(db, open_items, selected, now, release, menu_food_ids={i.food_id for i in items})
    return _compute_meal_cost(items, free, budget, selected.get, now, unquantified_food_ids, reasons, prepared)


def _source_quote(db: Session, item: IngredientRequirement, candidates: list[PriceQuote], now: datetime,
                  release: str | None) -> PriceQuote | None:
    """The quote another food would be priced with, including its published management decision."""
    quote = select_cost_quote(item, candidates, now)
    if not settings.management_price_policy_enabled:
        return quote
    decision = active_overrides(db, {item.food_id}, {item.food_id: quote}, as_of=now, recipe_release_id=release,
                                allow_volume=item.volume_ml is not None).get(item.food_id)
    if decision is None:
        return quote
    return None if decision.reason else decision.quote


def _estimate_line(item: IngredientRequirement, price: Decimal, quantity: Decimal, *, whole_package: bool,
                   basis: str, note: str, url: str | None, quote: PriceQuote | None = None) -> IngredientCost | None:
    """Estimated cost for a gram requirement; whole_package charges one package whatever the need."""
    if item.grams is None and not whole_package:
        return None
    if whole_package:
        packs, exact_cost = 1, Fraction(price)
    else:
        ratio = Fraction(item.grams) / Fraction(quantity)
        packs, exact_cost = ceil(ratio), ratio * Fraction(price)
    return IngredientCost(item.food_id, item.grams, item.grams, "estimated", quote=quote, packs=packs,
                          purchase_price=packs * price,
                          cost=Decimal(exact_cost.numerator) / Decimal(exact_cost.denominator),
                          required_ml=item.volume_ml, shortage_ml=item.volume_ml, exact_cost=exact_cost,
                          whole_package=whole_package, price_basis=basis, estimate_note=note, estimate_url=url)


LATEST_MARKET_ESTIMATES_SQL = text(SCOPED_QUOTES_SQL + """
    SELECT DISTINCT ON (food_id) * FROM scoped_quotes
    WHERE food_id IN :food_ids AND status = 'review_required'
      AND quote_payload ->> 'selection_method' = 'confirmed_product_rule'
      AND quote_payload ->> 'selection_tier' = 'estimate'
    ORDER BY food_id, queried_at DESC, id DESC
""").bindparams(bindparam("food_ids", expanding=True))


def _market_estimates(db: Session, food_ids: set[str]) -> dict[str, PriceQuote]:
    """Latest user-confirmed market estimate per food, even when an older store success exists."""
    from app.source_adapters.confirmed_products import load_confirmed_rules
    rules = load_confirmed_rules()
    food_ids = {food_id for food_id in food_ids if food_id in rules and rules[food_id].kind == "market_estimate"}
    if not food_ids:
        return {}
    return {row["food_id"]: materialize_quote(row)
            for row in db.execute(LATEST_MARKET_ESTIMATES_SQL, {"food_ids": sorted(food_ids)}).mappings()}


def _historical_reason(quote: PriceQuote | None, now: datetime) -> bool:
    """A store success that is unusable only because it expired (still TWD, grams, valid package)."""
    return (quote is not None and quote.status is QuoteStatus.SUCCESS and quote.price is not None
            and quote_unusable_reason(quote, now) == "PRICE_EXPIRED"
            and quote_unusable_reason(quote.model_copy(update={"valid_until": None}), now) is None)


def _price_by_supplements(db: Session, items: list[IngredientRequirement],
                          selected: dict[IngredientRequirement, PriceQuote | None], now: datetime,
                          release: str | None, *, menu_food_ids: set[str]) -> dict[IngredientRequirement, IngredientCost]:
    """Lines for foods with no usable quote of their own, in order: included in another menu food,
    derived from a source food's live quote, user-confirmed market estimate, the last (expired) store price,
    cited estimate."""
    from app.services.price_supplements import load_supplements

    supplements = load_supplements()
    missing = [item for item in items if price_requirement(item, selected.get(item), now).price_status != "known"]
    if not missing:
        return {}
    derived = {item: supplements.derived[item.food_id] for item in missing if item.food_id in supplements.derived}
    candidates = cost_quote_candidates(db, {rule.source_food_id for rule in derived.values()}) if derived else {}
    market = _market_estimates(db, {item.food_id for item in missing})
    prepared: dict[IngredientRequirement, IngredientCost] = {}
    for item in missing:
        rule = derived.get(item)
        if rule is not None and menu_food_ids & set(rule.included_if_in_menu):
            prepared[item] = IngredientCost(
                item.food_id, item.grams, ZERO if item.grams is not None else None, "included", packs=0,
                purchase_price=ZERO, cost=ZERO, required_ml=item.volume_ml,
                shortage_ml=Fraction() if item.volume_ml is not None else None, price_basis="included",
                derived_from_food_id=next(i for i in rule.included_if_in_menu if i in menu_food_ids),
                estimate_note=f"已含在本餐的{rule.source_name}：{rule.basis}")
            continue
        if rule is not None and item.grams is not None:
            source = IngredientRequirement(rule.source_food_id, item.grams * rule.ratio)
            line = price_requirement(
                source, _source_quote(db, source, candidates.get(rule.source_food_id, []), now, release), now)
            if line.price_status == "known":
                prepared[item] = replace(
                    line, food_id=item.food_id, required_grams=item.grams, shortage_grams=item.grams,
                    required_ml=item.volume_ml, shortage_ml=item.volume_ml,
                    price_status="known" if rule.ratio == 1 and not rule.estimate else "estimated", price_basis="derived",
                    derived_from_food_id=rule.source_food_id,
                    estimate_note=f"依{rule.source_name}價格換算（每 1 g 以 {rule.ratio:f} g {rule.source_name}計）：{rule.basis}")
                continue
        estimate_quote = market.get(item.food_id)
        if estimate_quote is not None and estimate_quote.estimated_price:
            quantity = estimate_quote.package_quantity or _confirmed_quantity(item.food_id)
            line = quantity and _estimate_line(
                item, estimate_quote.estimated_price, quantity, whole_package=False, basis="market_estimate",
                quote=estimate_quote, url=estimate_quote.source_url,
                note=f"行情估價（{estimate_quote.estimated_product_name or estimate_quote.source}，"
                     f"{estimate_quote.queried_at:%Y-%m-%d}）")
            if line:
                prepared[item] = line
                continue
        quote = selected.get(item)
        if _historical_reason(quote, now) and quote.package_unit == "g" and quote.package_quantity:
            # 使用者 2026-11-16：查不到當日價時以歷史資料補上；標示為歷史價格估價，不當作當日售價
            line = _estimate_line(
                item, quote.price, quote.package_quantity, whole_package=False, basis="historical", quote=quote,
                url=quote.product_url or quote.source_url,
                note=f"歷史價格：{_source_label(quote)} {quote.queried_at:%Y-%m-%d} 的售價（今日未查到）")
            if line:
                prepared[item] = line
                continue
        estimate = supplements.estimates.get(item.food_id)
        if estimate is not None:
            line = _estimate_line(
                item, estimate.price, estimate.package_quantity, whole_package=estimate.whole_package,
                basis="estimate", url=estimate.source_url,
                note=f"估價：{estimate.source_name}（{estimate.observed}）{estimate.basis}")
            if line:
                prepared[item] = line
    return prepared


def _source_label(quote: PriceQuote) -> str:
    return {"PX Mart": "全聯"}.get(quote.retailer, quote.retailer)


def _confirmed_quantity(food_id: str) -> Decimal | None:
    from app.source_adapters.confirmed_products import load_confirmed_rules
    rule = load_confirmed_rules().get(food_id)
    return Decimal(rule.quantity) if rule and rule.unit == "g" else None


def _price_by_confirmed_synonym(db: Session, items: list[IngredientRequirement],
                                selected: dict[IngredientRequirement, PriceQuote | None], now: datetime,
                                release: str | None = None) -> None:
    """A food with no usable quote of its own takes a confirmed synonym's quote (e.g. 椰菜 = 高麗菜, SYN006).

    Only teammate-confirmed groups apply (synonyms.confirmed_equivalents), the same rule as
    owned ingredients; the quote keeps its own product and records which Food_ID priced it. The synonym's
    published management decision applies: a blocked synonym prices nothing.
    """
    missing = [item for item in items if price_requirement(item, selected.get(item), now).price_status != "known"]
    others = {item: sorted(confirmed_equivalents([item.food_id]) - {item.food_id}) for item in missing}
    candidates = cost_quote_candidates(db, {food_id for ids in others.values() for food_id in ids})
    for item, food_ids in others.items():
        for food_id in food_ids:
            synonym = replace(item, food_id=food_id)
            quote = _source_quote(db, synonym, candidates.get(food_id, []), now, release)
            if quote is not None and price_requirement(item, quote, now).price_status == "known":
                reason = f"confirmed synonym: priced by {food_id} ({quote.canonical_name})"
                selected[item] = quote.model_copy(update={"selection_reasons": [*quote.selection_reasons, reason]})
                break
