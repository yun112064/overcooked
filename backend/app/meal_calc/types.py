"""T12 → T13 共用介面。欄位須與 T12 完全一致，勿自行增減。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IngredientRequirement:
    """一項配方食材的整餐需求量：已乘 N 的生重克數，未取整。"""

    food_id: str
    grams: Decimal
