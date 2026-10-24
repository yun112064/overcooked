from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IngredientRequirement:
    """One canonical ingredient's unrounded whole-meal requirement in grams."""

    ingredient_id: str
    grams: Decimal
