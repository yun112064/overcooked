"""Persist immutable T04 parameter receipts bound to versioned SQL inputs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import RecipeDataRelease
from app.models.nutrition import (FoodNutrition, NutritionFoodMapping, NutritionImport,
                                  NutritionSource, T04CookingReceipt)


def persist_receipt(factory: sessionmaker, *, recipe_release_id: str, nutrition_import_id: str,
                    mapping_version: str, recipe_ingredient_id: str, food_id: str,
                    nutrition_id: str, nutrition_source_id: str, cooking_method: str,
                    weight_basis: str, weight_g, parameters: dict, source_version: str,
                    is_estimated: bool, assumptions: list[str], reference_ids: list[str],
                    context: dict | None = None, source_provider: str = "TFDA") -> str:
    """Validate the exact SQL lineage and persist T04 parameters; never nutrition totals."""
    if weight_basis not in {"raw", "cooked"} or not cooking_method.strip():
        raise ValueError("invalid T04 receipt input")
    weight = Decimal(str(weight_g))
    yield_factor = Decimal(str(parameters.get("yield_factor")))
    retention = {key: Decimal(str(value)) for key, value in parameters.get("retention", {}).items()}
    if (not weight.is_finite() or weight <= 0 or not yield_factor.is_finite()
            or yield_factor <= 0 or yield_factor > 10 or not source_version
            or not assumptions or not reference_ids
            or any(not value.is_finite() or value < 0 or value > 1 for value in retention.values())):
        raise ValueError("invalid T04 cooking parameters or provenance")
    with factory() as db, db.begin():
        release = db.get(RecipeDataRelease, recipe_release_id)
        imported = db.get(NutritionImport, nutrition_import_id)
        row = db.execute(select(NutritionFoodMapping, FoodNutrition, NutritionSource)
            .join(FoodNutrition, (FoodNutrition.nutrition_id == NutritionFoodMapping.nutrition_id)
                              & (FoodNutrition.import_id == NutritionFoodMapping.import_id))
            .join(NutritionSource, (NutritionSource.source_id == NutritionFoodMapping.source_id)
                                 & (NutritionSource.import_id == NutritionFoodMapping.import_id))
            .where(NutritionFoodMapping.recipe_release_id == recipe_release_id,
                   NutritionFoodMapping.import_id == nutrition_import_id,
                   NutritionFoodMapping.mapping_version == mapping_version,
                   NutritionFoodMapping.food_id == food_id,
                   NutritionFoodMapping.nutrition_id == nutrition_id,
                   NutritionFoodMapping.source_id == nutrition_source_id,
                   NutritionFoodMapping.approval_status == "approved")).one_or_none()
        if not release or not release.active or not imported or not row:
            raise ValueError("pinned recipe, nutrition import, or approved exact mapping unavailable")
        if source_provider != "TFDA" and not (
                source_provider == "SYNTHETIC_TEST"
                and imported.report and imported.report.get("synthetic") is True):
            raise ValueError("receipt source provider not allowed")
        if source_provider == "TFDA" and (imported.report or {}).get("synthetic") is True:
            raise ValueError("synthetic import cannot be used as TFDA")
        mapping, observation, source = row
        if source_provider == "SYNTHETIC_TEST" and (
                (source.raw_data or {}).get("source_label") != "SYNTHETIC_TEST_ONLY"
                or (mapping.evidence or {}).get("test_only") is not True):
            raise ValueError("synthetic receipt source is not marked test only")
        if (source.provider != source_provider or source.source_record_id != parameters.get("sample_id")
                or observation.food_id != food_id or observation.import_id != nutrition_import_id
                or observation.cooking_state != mapping.cooking_state
                or mapping.cooking_method != (None if weight_basis == "raw" else cooking_method)):
            raise ValueError("T04 receipt sample/state/method does not match approved SQL observation")
        recipe_hash = (imported.source_files or {}).get("recipes", {}).get("sha256")
        if recipe_hash != release.source_sha256:
            raise ValueError("recipe release and nutrition import source do not match")
        raw = source.raw_data or {}
        if raw.get("來源資料ID") != source.source_record_id or raw.get("熟製狀態") != mapping.cooking_state:
            raise ValueError("TFDA source row metadata mismatch")
        raw_weight = weight if weight_basis == "raw" else None
        cooked_weight = (weight * yield_factor if weight_basis == "raw" else weight)
        material = {"calculation_version": "t04-receipt-v1",
            "recipe_release_id": recipe_release_id, "nutrition_import_id": nutrition_import_id,
            "mapping_version": mapping_version, "recipe_ingredient_id": recipe_ingredient_id,
            "food_id": food_id, "nutrition_id": nutrition_id, "nutrition_source_id": nutrition_source_id,
            "sample_id": source.source_record_id, "cooking_state": mapping.cooking_state,
            "cooking_method": cooking_method, "weight_basis": weight_basis, "weight_g": str(weight),
            "raw_weight_g": str(raw_weight) if raw_weight is not None else None,
            "cooked_weight_g": str(cooked_weight),
            "parameters": {"yield_factor": str(yield_factor), "retention": {k: str(v) for k,v in retention.items()},
                           "sample_id": source.source_record_id, "cooking_method": cooking_method},
            "source_version": source_version, "is_estimated": bool(is_estimated),
            "assumptions": assumptions, "reference_ids": reference_ids,
            "missing_information": (context or {}).get("missing_information", []),
            "context": context or {}}
        input_digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        # Column contract is VARCHAR(64); reserve 4 chars for the stable namespace.
        receipt_id = "t04-" + hashlib.sha256((input_digest + source_version).encode()).hexdigest()[:60]
        existing = db.get(T04CookingReceipt, receipt_id)
        if existing:
            return existing.receipt_id
        receipt = T04CookingReceipt(receipt_id=receipt_id, recipe_release_id=recipe_release_id,
            nutrition_import_id=nutrition_import_id, mapping_version=mapping_version,
            recipe_ingredient_id=recipe_ingredient_id, food_id=food_id,
            nutrition_id=nutrition_id, nutrition_source_id=nutrition_source_id,
            sample_id=source.source_record_id, cooking_state=mapping.cooking_state,
            cooking_method=cooking_method, weight_basis=weight_basis, weight_g=weight,
            yield_factor=yield_factor, retention={k: str(v) for k,v in retention.items()},
            source_version=source_version, input_digest=input_digest, is_estimated=is_estimated,
            assumptions=list(assumptions), reference_ids=list(reference_ids), receipt=material,
            created_at=datetime.now(timezone.utc))
        try:
            with db.begin_nested():
                db.add(receipt)
                db.flush()
        except IntegrityError:
            # Another worker may have persisted the same receipt after our read.
            existing = db.get(T04CookingReceipt, receipt_id)
            if existing is None or existing.input_digest != input_digest:
                raise
        return receipt_id
