"""Bind a previously reviewed T04 cooking estimate to one exact SQL observation.

This adapter does not call an LLM and never approves a Food_ID mapping. It only
turns a persisted estimator result into a receipt after all pinned inputs match.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.db.models import ConversionFactorEstimate
from app.models import RecipeDataRelease, RecipeIngredient
from app.models.nutrition import (
    FoodNutrition, NutritionFoodMapping, NutritionSource, NutritionSourceLink, NutritionImport,
)
from app.services.t04_receipts import persist_receipt
from app.services.nutrition_release import recipe_import_matches


RAW_STATES = (
    "未熟製", "未熟製原料", "未熟製果類", "未熟製蛋原料", "未熟製／半成品",
    "未熟製原料；未熟製", "未熟製；未熟製原料", "未熟製；未熟製／半成品", "調理原料／未熟製",
)


def _decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite T04 parameter")
    return result


def produce_receipt_from_estimate(factory, *, recipe_release_id: str, nutrition_import_id: str,
                                  mapping_version: str, line, weight_g,
                                  source_provider: str = "TFDA") -> dict | None:
    """Persist and return a receipt for a raw recipe line, or None when evidence is insufficient."""
    if (not line.recipe_ingredient_id or not line.cooking_method or line.weight_basis != "raw"
            or line.consumed_as_is):
        return None
    from app.services.meal_calculation import normalize_cooking_method
    method = normalize_cooking_method(line.cooking_method)
    if not method:
        return None
    weight = _decimal(line.grams_per_n if line.grams_per_n is not None else weight_g)
    if weight <= 0:
        return None
    with factory() as db:
        ingredient = db.get(RecipeIngredient, line.recipe_ingredient_id)
        if (ingredient is None or ingredient.recipe_release_id != recipe_release_id
                or ingredient.food_id != line.food_id):
            return None
        query = (select(NutritionFoodMapping, FoodNutrition, NutritionSource)
            .join(FoodNutrition, (FoodNutrition.nutrition_id == NutritionFoodMapping.nutrition_id)
                              & (FoodNutrition.import_id == NutritionFoodMapping.import_id))
            .join(NutritionSourceLink, (NutritionSourceLink.nutrition_id == FoodNutrition.nutrition_id)
                                     & (NutritionSourceLink.source_id == NutritionFoodMapping.source_id)
                                     & NutritionSourceLink.included_in_calculation.is_(True))
            .join(NutritionSource, (NutritionSource.source_id == NutritionFoodMapping.source_id)
                                 & (NutritionSource.import_id == NutritionFoodMapping.import_id))
            .where(NutritionFoodMapping.recipe_release_id == recipe_release_id,
                   NutritionFoodMapping.import_id == nutrition_import_id,
                   NutritionFoodMapping.mapping_version == mapping_version,
                   NutritionFoodMapping.food_id == line.food_id,
                   NutritionFoodMapping.cooking_state.in_(RAW_STATES),
                   NutritionFoodMapping.cooking_method.is_(None),
                   NutritionFoodMapping.approval_status == "approved",
                   NutritionSource.provider == source_provider))
        matches = db.execute(query).all()
        if len(matches) != 1:
            return None
        mapping, observation, source = matches[0]
        if (observation.cooking_state != mapping.cooking_state or observation.cooking_method is not None
                or source.source_record_id != (source.raw_data or {}).get("來源資料ID")
                or mapping.cooking_state != (source.raw_data or {}).get("熟製狀態")
                or (source.raw_data or {}).get("納入平均") not in ("是", "Y", "yes", True)):
            return None
        recipe_import = db.get(NutritionImport, nutrition_import_id)
        release = db.get(RecipeDataRelease, recipe_release_id)
        if (recipe_import is None or release is None or not release.active
                or not recipe_import_matches(db, release, recipe_import, mapping_version=mapping_version)):
            return None
        if source_provider != "TFDA" and not (
                source_provider == "SYNTHETIC_TEST"
                and recipe_import.report and recipe_import.report.get("synthetic") is True):
            return None
        if source_provider == "TFDA" and (recipe_import.report or {}).get("synthetic") is True:
            return None
        if source_provider == "SYNTHETIC_TEST" and (
                (source.raw_data or {}).get("source_label") != "SYNTHETIC_TEST_ONLY"
                or (mapping.evidence or {}).get("test_only") is not True):
            return None
        estimate_query = (select(ConversionFactorEstimate)
            .where(ConversionFactorEstimate.kind == "cooking",
                   ConversionFactorEstimate.food_id == line.food_id,
                   ConversionFactorEstimate.factor_key == method,
                   ConversionFactorEstimate.status.in_(("llm_estimated", "approved", "overridden")),
                   ConversionFactorEstimate.yield_factor.is_not(None),
                   ConversionFactorEstimate.energy_retention.is_not(None),
                   ConversionFactorEstimate.protein_retention.is_not(None))
            .order_by(ConversionFactorEstimate.created_at.desc()))
        estimate = None
        for candidate in db.scalars(estimate_query):
            snapshot = candidate.input_snapshot or {}
            raw_sample = (snapshot.get("tfda_raw") or {}).get("sample_id")
            if (raw_sample == source.source_record_id
                    and snapshot.get("recipe_release_id") == recipe_release_id
                    and snapshot.get("nutrition_import_id") == nutrition_import_id
                    and snapshot.get("mapping_version") == mapping_version):
                estimate = candidate
                break
        if estimate is None or not estimate.assumptions:
            return None
        snapshot = estimate.input_snapshot or {}
        refs = [str(ref.get("reference_id")) for ref in snapshot.get("usda_yield_references", [])
                if isinstance(ref, dict) and ref.get("reference_id")]
        if not refs:
            # This is an audit pointer to the persisted T04 result, not a claim of an external source.
            refs = [f"conversion-factor-estimate:{estimate.id}"]
        try:
            yield_factor = _decimal(estimate.yield_factor)
            energy = _decimal(estimate.energy_retention)
            protein = _decimal(estimate.protein_retention)
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not (Decimal("0.3") <= yield_factor <= Decimal("4")
                and Decimal("0.5") <= energy <= Decimal("1")
                and Decimal("0.5") <= protein <= Decimal("1")):
            return None
        retention = {"energy_kcal": str(energy), "protein_g": str(protein)}
        extra_retention = snapshot.get("retention") or {}
        for nutrient in ("fat_g", "carbohydrate_g", "sugars_g", "sodium_mg", "dietary_fiber_g"):
            if nutrient in extra_retention:
                try:
                    value = _decimal(extra_retention[nutrient])
                except (InvalidOperation, TypeError, ValueError):
                    return None
                if not Decimal("0") <= value <= Decimal("1"):
                    return None
                retention[nutrient] = str(value)
        parameters = {"sample_id": source.source_record_id, "cooking_method": line.cooking_method,
                      "yield_factor": str(yield_factor),
                      "retention": retention}
        source_version = f"conversion-factor-estimate:{estimate.id}"
        context = {"estimate_id": str(estimate.id), "estimate_source": estimate.source,
                   "estimate_status": estimate.status, "model": estimate.model,
                   "prompt_version": estimate.prompt_version, "schema_version": estimate.schema_version,
                   "call_key": estimate.call_key, "input_snapshot": snapshot,
                   "retention_fields_available": list(retention),
                   "missing_information": [key for key in ("fat_g", "carbohydrate_g", "sugars_g",
                       "sodium_mg", "dietary_fiber_g") if key not in retention]}
        assumptions = list(estimate.assumptions)
        nutrition_id, source_id = observation.nutrition_id, source.source_id
    receipt_id = persist_receipt(factory, recipe_release_id=recipe_release_id,
        nutrition_import_id=nutrition_import_id, mapping_version=mapping_version,
        recipe_ingredient_id=line.recipe_ingredient_id, food_id=line.food_id,
        nutrition_id=nutrition_id, nutrition_source_id=source_id,
        cooking_method=line.cooking_method, weight_basis="raw", weight_g=weight,
        parameters=parameters, source_version=source_version, is_estimated=True,
        assumptions=assumptions, reference_ids=refs, context=context,
        source_provider=source_provider)
    return {"recipe_release_id": recipe_release_id, "nutrition_import_id": nutrition_import_id,
            "mapping_version": mapping_version, "recipe_ingredient_id": line.recipe_ingredient_id,
            "food_id": line.food_id, "sample_id": source.source_record_id,
            "nutrition_id": nutrition_id, "nutrition_source_id": source_id,
            "cooking_state": mapping.cooking_state, "cooking_method": line.cooking_method,
            "weight_basis": "raw", "weight_g": str(weight), "parameters": parameters,
            "source_version": source_version, "is_estimated": True,
            "assumptions": assumptions, "reference_ids": refs, "receipt_id": receipt_id}
