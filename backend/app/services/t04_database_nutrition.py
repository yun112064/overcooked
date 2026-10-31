"""T04 calculations backed by one explicit version of imported FOOD nutrition.

This adapter intentionally requires the caller to choose the import, Food_ID,
original cooking state and cooking method.  It never guesses another variant and
never falls back to the legacy JSON data, so one calculation cannot silently mix
nutrition versions.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nutrition import NutritionSource, NutritionSourceLink
from app.services.nutrition_import import NUTRIENT_COLUMNS, find_nutrition_candidates


NUTRIENT_FIELDS = tuple(NUTRIENT_COLUMNS.values())


def _positive_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return number


def _sources(session: Session, nutrition_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(NutritionSource, NutritionSourceLink.included_in_calculation)
        .join(NutritionSourceLink, NutritionSourceLink.source_id == NutritionSource.source_id)
        .where(NutritionSourceLink.nutrition_id == nutrition_id)
        .order_by(NutritionSource.source_key)
    ).all()
    return [
        {
            "source_id": source.source_id,
            "source_key": source.source_key,
            "provider": source.provider,
            "source_record_id": source.source_record_id,
            "source_url": source.source_url,
            "source_sheet": source.source_sheet,
            "source_row": source.source_row,
            "included_in_calculation": included,
        }
        for source, included in rows
    ]


def calculate_food_nutrition(
    session: Session,
    *,
    import_id: str,
    food_id: str,
    cooking_state: str,
    cooking_method: str | None,
    weight_g: Any,
    round_result: bool = True,
) -> dict[str, Any]:
    """Scale one exact imported observation to ``weight_g``.

    ``weight_g`` must use the same basis as the selected observation.  Cooking
    yield conversion remains a separate T04 step and is never inferred here.
    """
    try:
        weight = _positive_decimal(weight_g, "weight_g")
    except ValueError:
        return {"success": False, "error": "invalid_nutrition_input"}
    if not all(isinstance(value, str) and value.strip() for value in (import_id, food_id, cooking_state)):
        return {"success": False, "error": "invalid_nutrition_input"}
    if cooking_method is not None and (not isinstance(cooking_method, str) or not cooking_method.strip()):
        return {"success": False, "error": "invalid_nutrition_input"}

    rows = find_nutrition_candidates(
        session,
        import_id=import_id,
        food_id=food_id,
        cooking_state=cooking_state,
        cooking_method=cooking_method,
    )
    if not rows:
        return {"success": False, "error": "nutrition_observation_not_found"}
    if len(rows) != 1:
        return {"success": False, "error": "ambiguous_nutrition_observation"}

    row = rows[0]
    ratio = weight / Decimal("100")
    exact = {
        field: (getattr(row, field) * ratio if getattr(row, field) is not None else None)
        for field in NUTRIENT_FIELDS
    }
    missing = [field for field, value in exact.items() if value is None]
    shown = {
        field: (value.quantize(Decimal("0.01")) if round_result and value is not None else value)
        for field, value in exact.items()
    }
    return {
        "success": True,
        "import_id": row.import_id,
        "nutrition_id": row.nutrition_id,
        "food_id": row.food_id,
        "standard_name": row.standard_name,
        "cooking_state": row.cooking_state,
        "cooking_method": row.cooking_method,
        "preparation": row.preparation,
        "calculation_method": row.calculation_method,
        "weight_g": weight,
        "nutrition": shown,
        "nutrition_unrounded": exact,
        "nutrition_complete": not missing,
        "missing_fields": missing,
        "quality_issues": list(row.quality_issues),
        "sources": _sources(session, row.nutrition_id),
        "nutrition_basis": "imported_food_observation",
        "cooking_adjustment_complete": True,
        "is_estimated": False,
    }


def calculate_recipe_nutrition(
    session: Session,
    *,
    import_id: str,
    recipe_name: str,
    recipe_ingredients: list[dict[str, Any]],
    servings: Any = 1,
) -> dict[str, Any]:
    """Aggregate exact imported observations for one recipe and one version."""
    try:
        serving_count = _positive_decimal(servings, "servings")
    except ValueError:
        return {"success": False, "error": "invalid_recipe_input"}
    if not isinstance(recipe_name, str) or not recipe_name.strip() or not recipe_ingredients:
        return {"success": False, "error": "invalid_recipe_input"}

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in recipe_ingredients:
        if not isinstance(item, dict):
            result = {"success": False, "error": "invalid_ingredient_input"}
            failed.append({"food_id": None, "error": result["error"]})
            results.append(result)
            continue
        result = calculate_food_nutrition(
            session,
            import_id=import_id,
            food_id=item.get("food_id"),
            cooking_state=item.get("cooking_state"),
            cooking_method=item.get("cooking_method"),
            weight_g=item.get("weight_g"),
            round_result=False,
        )
        results.append(result)
        if not result.get("success"):
            failed.append({"food_id": item.get("food_id"), "error": result.get("error")})

    missing = {
        field: [result.get("food_id") for result in results
                if result.get("success") and result["nutrition_unrounded"][field] is None]
        for field in NUTRIENT_FIELDS
    }
    exact = {
        field: None if failed or missing[field] else sum(
            (result["nutrition_unrounded"][field] for result in results), Decimal("0")
        )
        for field in NUTRIENT_FIELDS
    }
    return {
        "success": not failed,
        "recipe_name": recipe_name,
        "import_id": import_id,
        "ingredients": results,
        "total_nutrition": {
            field: (value.quantize(Decimal("0.01")) if value is not None else None)
            for field, value in exact.items()
        },
        "total_nutrition_unrounded": exact,
        "per_serving_nutrition": {
            field: ((value / serving_count).quantize(Decimal("0.01")) if value is not None else None)
            for field, value in exact.items()
        },
        "nutrition_complete": not failed and all(value is not None for value in exact.values()),
        "cooking_adjustment_complete": not failed,
        "missing_nutrients": {field: ids for field, ids in missing.items() if ids},
        "failed_ingredients": failed,
        "servings": serving_count,
        "data_source": "database",
    }
