"""T04 calculations backed by one explicit version of imported FOOD nutrition.

This adapter intentionally requires the caller to choose the import, Food_ID,
original cooking state and cooking method.  It never guesses another variant and
never falls back to the legacy JSON data, so one calculation cannot silently mix
nutrition versions.

``scale_per_100g`` is the single per-100g → weight conversion.  T12
(``nutrition_planning.service.calculate_nutrition``) calls it for every line, so
T04 and T12 always give identical numbers for the same sample and weight.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
from app.services.nutrition_import import NUTRIENT_COLUMNS, _references, find_nutrition_candidates
from app.services.recipe_import import WorkbookValidationError


NUTRIENT_FIELDS = tuple(NUTRIENT_COLUMNS.values())
# P03 (2026-11-04): adoption requires energy and protein, matching T12's REQUIRED_TARGETS.
# The other five are display-only; their gaps stay visible in missing_fields.
REQUIRED_NUTRIENTS = ("energy_kcal", "protein_g")
# Weight basis each imported cooking state is measured on; other states stay unverified.
STATE_WEIGHT_BASIS = {"未熟製": "raw", "已熟製": "cooked"}


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


def scale_per_100g(per_100g: Mapping[str, Any], weight_g: Any,
                   retention: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Scale per-100g values to ``weight_g``, optionally times a per-nutrient retention.

    Exact arithmetic in the caller's number type (Decimal in → Decimal out,
    Fraction in → Fraction out).  A missing value or retention stays None, never 0.
    """
    ratio = weight_g / 100
    return {field: (None if value is None or (retention is not None and retention.get(field) is None)
                    else value * ratio * (1 if retention is None else retention[field]))
            for field, value in per_100g.items()}


def _source_map(session: Session, nutrition_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Source links for many observations in one query, keyed by nutrition ID."""
    found: dict[str, list[dict[str, Any]]] = {nutrition_id: [] for nutrition_id in nutrition_ids}
    if not nutrition_ids:
        return found
    rows = session.execute(
        select(NutritionSourceLink.nutrition_id, NutritionSource, NutritionSourceLink.included_in_calculation)
        .join(NutritionSourceLink, NutritionSourceLink.source_id == NutritionSource.source_id)
        .where(NutritionSourceLink.nutrition_id.in_(nutrition_ids))
        .order_by(NutritionSource.source_key)
    ).all()
    for nutrition_id, source, included in rows:
        found[nutrition_id].append({
            "source_id": source.source_id,
            "source_key": source.source_key,
            "provider": source.provider,
            "source_record_id": source.source_record_id,
            "source_url": source.source_url,
            "source_sheet": source.source_sheet,
            "source_row": source.source_row,
            "included_in_calculation": included,
        })
    return found


class _RecipeLookup:
    """Observations and sources for one recipe, loaded in two queries instead of two per line."""

    def __init__(self, session: Session, import_id: str, food_ids: set[str]):
        rows = list(session.scalars(select(FoodNutrition).where(
            FoodNutrition.import_id == import_id, FoodNutrition.food_id.in_(food_ids),
        ).order_by(FoodNutrition.source_row))) if food_ids else []
        self._rows = rows
        self._sources = _source_map(session, {row.nutrition_id for row in rows})

    def candidates(self, food_id: str, cooking_state: str, cooking_method: str | None) -> list[FoodNutrition]:
        return [row for row in self._rows if row.food_id == food_id and row.cooking_state == cooking_state
                and row.cooking_method == cooking_method]

    def sources(self, nutrition_id: str) -> list[dict[str, Any]]:
        return self._sources[nutrition_id]


def calculate_food_nutrition(
    session: Session,
    *,
    import_id: str,
    food_id: str | None,
    cooking_state: str | None,
    cooking_method: str | None,
    weight_g: Any,
    weight_basis: str | None = None,
    consumed_as_is: bool = False,
    round_result: bool = True,
    _lookup: _RecipeLookup | None = None,
) -> dict[str, Any]:
    """Scale one exact imported observation to ``weight_g``.

    ``weight_g`` must use the same basis as the selected observation.  Cooking
    yield conversion remains a separate T04 step and is never inferred here, so
    the cooking basis is only complete when ``weight_basis`` ("raw"/"cooked")
    is declared and matches the observation's state, a raw observation is
    declared ``consumed_as_is``, and at least one source fed the calculation.
    """
    if weight_basis is not None and weight_basis not in ("raw", "cooked"):
        return {"success": False, "error": "invalid_weight_basis"}
    if not isinstance(consumed_as_is, bool):
        return {"success": False, "error": "invalid_consumed_as_is"}
    try:
        weight = _positive_decimal(weight_g, "weight_g")
    except ValueError:
        return {"success": False, "error": "invalid_nutrition_input"}
    if not (isinstance(food_id, str) and isinstance(cooking_state, str)) or not all(
            isinstance(value, str) and value.strip() for value in (import_id, food_id, cooking_state)):
        return {"success": False, "error": "invalid_nutrition_input"}
    if cooking_method is not None and (not isinstance(cooking_method, str) or not cooking_method.strip()):
        return {"success": False, "error": "invalid_nutrition_input"}

    if _lookup is not None:
        rows = _lookup.candidates(food_id, cooking_state, cooking_method)
    else:
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
    observation_basis = STATE_WEIGHT_BASIS.get(row.cooking_state)
    if weight_basis is not None and observation_basis is not None and weight_basis != observation_basis:
        return {"success": False, "error": "weight_basis_mismatch"}
    basis_verified = weight_basis is not None and weight_basis == observation_basis
    sources = (_lookup.sources(row.nutrition_id) if _lookup is not None
               else _source_map(session, {row.nutrition_id})[row.nutrition_id])
    # Every declared calculation source must still be linked; the importer drops links to recipe
    # lines that were removed or changed, so a partial set stays unverified.
    try:
        declared = set(_references(row.raw_data.get("計算來源ID")))
    except WorkbookValidationError:
        declared = set()
    linked = {source["source_key"] for source in sources if source["included_in_calculation"]}
    source_verified = bool(declared) and declared == linked
    if not basis_verified:
        missing_reason = "weight_basis_unverified"
    elif not source_verified:
        missing_reason = "missing_calculation_source"
    elif observation_basis == "raw" and not consumed_as_is:
        missing_reason = "no_cooking_basis; raw composition used as supplied"
    else:
        missing_reason = None
    scaled = scale_per_100g({field: getattr(row, field) for field in NUTRIENT_FIELDS}, weight)
    # Without a verified basis the scaled values are reference only (AC13): the official
    # nutrition stays unknown, so recipe totals and completeness cannot use it.
    exact = scaled if missing_reason is None else dict.fromkeys(NUTRIENT_FIELDS)
    missing = [field for field, value in exact.items() if value is None]
    shown = {
        field: (value.quantize(Decimal("0.01")) if round_result and value is not None else value)
        for field, value in exact.items()
    }
    reference = {} if missing_reason is None else {
        "reference_nutrition": {
            field: (value.quantize(Decimal("0.01")) if round_result and value is not None else value)
            for field, value in scaled.items()
        },
        "reference_nutrition_unrounded": scaled,
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
        "required_nutrients": list(REQUIRED_NUTRIENTS),
        "required_nutrition_complete": all(exact[field] is not None for field in REQUIRED_NUTRIENTS),
        "missing_fields": missing,
        "quality_issues": list(row.quality_issues),
        "sources": sources,
        "nutrition_basis": "imported_food_observation",
        "weight_basis_verified": basis_verified,
        "source_basis_verified": source_verified,
        "cooking_adjustment_complete": missing_reason is None,
        "missing_reason": missing_reason,
        **reference,
        "is_estimated": False,
    }


def calculate_source_nutrition(
    session: Session,
    *,
    import_id: str,
    nutrition_source_id: str,
    weight_g: Any,
    round_result: bool = True,
) -> dict[str, Any]:
    """Scale one pinned TFDA／USDA source sample (not the All aggregate) to ``weight_g``.

    This is the entry T12 uses: it selects one official sample per line, and the
    sample's own per-100g cells are converted here with ``scale_per_100g``.
    """
    try:
        weight = _positive_decimal(weight_g, "weight_g")
    except ValueError:
        return {"success": False, "error": "invalid_nutrition_input"}
    source = session.get(NutritionSource, nutrition_source_id) if isinstance(nutrition_source_id, str) else None
    if source is None or source.import_id != import_id:
        return {"success": False, "error": "nutrition_source_not_found"}
    try:
        per_100g = {field: _source_value(source.raw_data.get(column))
                    for column, field in NUTRIENT_COLUMNS.items()}
    except WorkbookValidationError:
        return {"success": False, "error": "invalid_source_nutrient"}
    exact = scale_per_100g(per_100g, weight)
    missing = [field for field, value in exact.items() if value is None]
    return {
        "success": True,
        "import_id": source.import_id,
        "nutrition_source_id": source.source_id,
        "provider": source.provider,
        "source_record_id": source.source_record_id,
        "weight_g": weight,
        "nutrition": {field: (value.quantize(Decimal("0.01")) if round_result and value is not None else value)
                      for field, value in exact.items()},
        "nutrition_unrounded": exact,
        "nutrition_complete": not missing,
        "required_nutrients": list(REQUIRED_NUTRIENTS),
        "required_nutrition_complete": all(exact[field] is not None for field in REQUIRED_NUTRIENTS),
        "missing_fields": missing,
        "nutrition_basis": "imported_source_sample",
        "is_estimated": False,
    }


def _source_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise WorkbookValidationError(f"Invalid source nutrient: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise WorkbookValidationError(f"Invalid source nutrient: {value!r}")
    return number


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

    # An invalid import_id skips the batch query; each line then reports invalid_nutrition_input.
    lookup = _RecipeLookup(session, import_id, {
        item["food_id"] for item in recipe_ingredients
        if isinstance(item, dict) and isinstance(item.get("food_id"), str)}
    ) if isinstance(import_id, str) and import_id.strip() else None
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
            weight_basis=item.get("weight_basis"),
            consumed_as_is=item.get("consumed_as_is", False),
            round_result=False,
            _lookup=lookup,
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
        "required_nutrients": list(REQUIRED_NUTRIENTS),
        "required_nutrition_complete": not failed and all(exact[field] is not None for field in REQUIRED_NUTRIENTS),
        "cooking_adjustment_complete": not failed and all(r["cooking_adjustment_complete"] for r in results),
        "missing_nutrients": {field: ids for field, ids in missing.items() if ids},
        "failed_ingredients": failed,
        "servings": serving_count,
        "data_source": "database",
    }
