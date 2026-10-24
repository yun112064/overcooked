"""Ticket 13: missing ingredients, package spend, proportional cost and budget."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from app.meal_calc.types import IngredientRequirement
from app.pricing_sources.repository import RetailPricingRepository
from app.pricing_sources.quotes import RetailPriceQuote

ZERO = Decimal("0")
QuoteLookup = Callable[[str], RetailPriceQuote | None]

BUDGET_OK = "OK"
PRICE_INCOMPLETE = "PRICE_INCOMPLETE"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(frozen=True)
class IngredientCost:
    ingredient_id: str
    required_grams: Decimal
    shortage_grams: Decimal
    price_status: str
    quote: RetailPriceQuote | None = None
    packages: int | None = None
    purchase_price: Decimal | None = None
    proportional_cost: Decimal | None = None
    missing_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MealCostResult:
    lines: tuple[IngredientCost, ...]
    budget: Decimal
    known_purchase_total: Decimal
    known_cost_total: Decimal
    price_complete: bool
    budget_status: str

    @property
    def missing_items(self) -> tuple[IngredientCost, ...]:
        return tuple(line for line in self.lines if line.price_status != "owned")

    @property
    def purchase_total(self) -> Decimal | None:
        return self.known_purchase_total if self.price_complete else None

    @property
    def cost_total(self) -> Decimal | None:
        return self.known_cost_total if self.price_complete else None

    def to_dict(self) -> dict:
        return {
            "items": [line.to_dict() for line in self.missing_items],
            "budget": self.budget,
            "known_purchase_total": self.known_purchase_total,
            "known_cost_total": self.known_cost_total,
            "purchase_total": self.purchase_total,
            "cost_total": self.cost_total,
            "price_complete": self.price_complete,
            "budget_status": self.budget_status,
        }


def _aggregate_requirements(
    requirements: Iterable[IngredientRequirement],
) -> tuple[IngredientRequirement, ...]:
    totals: dict[str, Decimal] = {}
    for requirement in requirements:
        ingredient_id = requirement.ingredient_id.strip()
        if not ingredient_id:
            raise ValueError("ingredient_id must not be blank")
        grams = Decimal(requirement.grams)
        if not grams.is_finite() or grams <= ZERO:
            raise ValueError("ingredient grams must be a positive finite Decimal")
        totals[ingredient_id] = totals.get(ingredient_id, ZERO) + grams
    return tuple(
        IngredientRequirement(ingredient_id, grams)
        for ingredient_id, grams in totals.items()
    )


def compute_meal_cost(
    requirements: Iterable[IngredientRequirement],
    available_ingredient_ids: Iterable[str],
    budget: Decimal,
    quote_for: QuoteLookup,
    *,
    now: datetime,
) -> MealCostResult:
    """Calculate with Decimal only; package leftovers never alter meal grams."""

    budget = Decimal(budget)
    if not budget.is_finite() or budget < ZERO:
        raise ValueError("budget must be a non-negative finite Decimal")
    owned = {value.strip() for value in available_ingredient_ids if value.strip()}
    lines: list[IngredientCost] = []

    # Isolate arithmetic from a caller that changed the process Decimal context.
    with localcontext() as context:
        context.prec = 38
        for requirement in _aggregate_requirements(requirements):
            if requirement.ingredient_id in owned:
                lines.append(
                    IngredientCost(
                        ingredient_id=requirement.ingredient_id,
                        required_grams=requirement.grams,
                        shortage_grams=ZERO,
                        price_status="owned",
                        packages=0,
                        purchase_price=ZERO,
                        proportional_cost=ZERO,
                    )
                )
                continue

            quote = quote_for(requirement.ingredient_id)
            if quote is None:
                lines.append(
                    IngredientCost(
                        ingredient_id=requirement.ingredient_id,
                        required_grams=requirement.grams,
                        shortage_grams=requirement.grams,
                        price_status="unknown",
                        missing_reason="NO_VALID_PRICE",
                    )
                )
                continue
            if quote.valid_until is not None and quote.valid_until < now:
                lines.append(
                    IngredientCost(
                        ingredient_id=requirement.ingredient_id,
                        required_grams=requirement.grams,
                        shortage_grams=requirement.grams,
                        price_status="unknown",
                        quote=quote,
                        missing_reason="PRICE_EXPIRED",
                    )
                )
                continue
            if quote.package_weight_g <= ZERO or quote.package_price <= ZERO:
                lines.append(
                    IngredientCost(
                        ingredient_id=requirement.ingredient_id,
                        required_grams=requirement.grams,
                        shortage_grams=requirement.grams,
                        price_status="unknown",
                        quote=quote,
                        missing_reason="INVALID_PACKAGE",
                    )
                )
                continue

            ratio = requirement.grams / quote.package_weight_g
            packages = int(ratio.to_integral_value(rounding=ROUND_CEILING))
            lines.append(
                IngredientCost(
                    ingredient_id=requirement.ingredient_id,
                    required_grams=requirement.grams,
                    shortage_grams=requirement.grams,
                    price_status="known",
                    quote=quote,
                    packages=packages,
                    purchase_price=quote.package_price * packages,
                    proportional_cost=quote.package_price * ratio,
                )
            )

        complete = all(line.proportional_cost is not None for line in lines)
        known_purchase = sum(
            (line.purchase_price for line in lines if line.purchase_price is not None),
            ZERO,
        )
        known_cost = sum(
            (
                line.proportional_cost
                for line in lines
                if line.proportional_cost is not None
            ),
            ZERO,
        )

    if known_cost > budget:
        budget_status = BUDGET_EXCEEDED
    elif not complete:
        budget_status = PRICE_INCOMPLETE
    else:
        budget_status = BUDGET_OK

    return MealCostResult(
        lines=tuple(lines),
        budget=budget,
        known_purchase_total=known_purchase,
        known_cost_total=known_cost,
        price_complete=complete,
        budget_status=budget_status,
    )


def compute_meal_cost_from_db(
    db: Session,
    requirements: Iterable[IngredientRequirement],
    available_ingredient_ids: Iterable[str],
    budget: Decimal,
    *,
    now: datetime,
) -> MealCostResult:
    """Backend entry point; callers must establish household authorization first."""

    repository = RetailPricingRepository(db)
    return compute_meal_cost(
        requirements,
        available_ingredient_ids,
        budget,
        lambda ingredient_id: repository.latest_valid_quote(
            ingredient_id, now=now
        ),
        now=now,
    )
