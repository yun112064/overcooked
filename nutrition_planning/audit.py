"""Read-only T12 audit for the imported Food_ID nutrition contract."""
from __future__ import annotations

from collections import Counter
from typing import TypedDict

from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from .imported_nutrition import NutritionDataUnavailable, cooking_state_kind


class FoodIdAuditReport(TypedDict):
    import_id: str
    recipe_food_id_count: int
    nutrition_food_id_count: int
    missing_food_ids: list[str]
    multi_observation_food_ids: list[str]
    cooking_state_counts: dict[str, int]
    unknown_cooking_states: list[str]
    ready_for_food_id_resolution: bool


def audit_food_id_coverage(db: Connection, *, import_id: str) -> FoodIdAuditReport:
    """Report whether every recipe Food_ID has imported nutrition observations.

    This deliberately does not change data or silently classify unknown states.
    It is suitable for the 434-Food_ID deployment audit and returns the exact
    Food_IDs requiring data review.
    """
    if not isinstance(import_id, str) or not import_id.strip():
        raise ValueError('import_id is required')
    try:
        meta = MetaData()
        ingredients = Table('recipe_ingredients', meta, autoload_with=db)
        nutrition = Table('food_nutrition', meta, autoload_with=db)
        recipe_food_ids = sorted({row['food_id'] for row in db.execute(
            select(ingredients.c.food_id)).mappings()})
        rows = tuple(db.execute(select(nutrition.c.food_id, nutrition.c.cooking_state,
                                       nutrition.c.nutrition_id)
                                .where(nutrition.c.import_id == import_id)).mappings())
    except (SQLAlchemyError, AttributeError) as exc:
        raise NutritionDataUnavailable() from exc
    nutrition_food_ids = {row['food_id'] for row in rows}
    states = Counter(str(row['cooking_state']) for row in rows)
    unknown_states = sorted({state for state in states if cooking_state_kind(state) is None})
    per_food = Counter(row['food_id'] for row in rows)
    return {
        'import_id': import_id,
        'recipe_food_id_count': len(recipe_food_ids),
        'nutrition_food_id_count': len(nutrition_food_ids),
        'missing_food_ids': sorted(set(recipe_food_ids) - nutrition_food_ids),
        'multi_observation_food_ids': sorted(food_id for food_id, count in per_food.items() if count > 1),
        'cooking_state_counts': dict(sorted(states.items())),
        'unknown_cooking_states': unknown_states,
        'ready_for_food_id_resolution': not (set(recipe_food_ids) - nutrition_food_ids) and not unknown_states,
    }
