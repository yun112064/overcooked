"""Trusted backend bridge from T01/T11 recipe IDs and T03/T04 to T12.

No HTTP, adoption, RAG, or generation orchestration. Profiles and NutritionPolicy
must come from the backend's reviewed data, never from browser/AI assertions.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Literal, cast

from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from .service import (IngredientQuantity, MealValidationResult, Number,
                      NutritionPolicy, RecipeCandidate, exact, validate_meal)
from .targets import MealTargets, PlanningInputError, targets_from_t03


class RecipeDataUnavailable(Exception):
    """DB/schema failure, distinct from incomplete or infeasible recipe data."""
    code = "RECIPE_DATA_UNAVAILABLE"


@dataclass(frozen=True)
class RecipeRows:
    recipe_ids: tuple[str, ...]
    recipes: tuple[Mapping, ...]
    ingredients: tuple[Mapping, ...]


@dataclass(frozen=True)
class InputIssue:
    code: str
    recipe_id: str
    line_id: str | None = None
    field: str | None = None
    input_digest: str | None = None


@dataclass(frozen=True)
class RecipeLineProfile:
    """Reviewed line-specific units/state; digest prevents stale reuse."""
    food_id: str
    input_digest: str
    weight_basis: str
    consumed_as_is: bool
    cooking_step_g: Number | None
    source: str
    source_version: str
    review_status: str = "pending"
    cooking_method: str | None = None
    evidence_id: str | None = None
    cooking_context: Mapping[str, str | bool] | None = None
    # Explicit reviewed conversions, required if DB grams/range are unverified.
    reviewed_weight_g: Number | None = None
    reviewed_min_g: Number | None = None
    reviewed_max_g: Number | None = None


@dataclass(frozen=True)
class PreparedRecipe:
    recipe: RecipeCandidate | None
    issues: tuple[InputIssue, ...]


@dataclass(frozen=True)
class DatabaseMenuResult:
    targets: MealTargets
    preparation: PreparedRecipe
    validation: MealValidationResult | None

    @property
    def final_food_ids(self) -> tuple[str, ...]:
        """Only validated final ingredients, for the caller's later RAG step."""
        if not self.validation or not self.validation.nutrition_passed or not self.validation.final_recipe:
            return ()
        return tuple(sorted({line.food_id for line in self.validation.final_recipe.ingredients
                             if exact(line.amount_g) > 0}))


def load_database_recipe_rows(db: Connection, recipe_ids: Sequence[str]) -> RecipeRows:
    if (isinstance(recipe_ids, (str, bytes)) or not recipe_ids
            or any(not isinstance(i, str) or not i.strip() for i in recipe_ids)
            or len(set(recipe_ids)) != len(recipe_ids)):
        raise PlanningInputError("Provide distinct recipe IDs selected by T11.")
    ids = tuple(recipe_ids)
    try:
        metadata = MetaData()
        recipes = Table("recipes", metadata, autoload_with=db)
        ingredients = Table("recipe_ingredients", metadata, autoload_with=db)
        # Read every line: available ingredients, oil and seasonings included.
        recipe_rows = tuple(dict(r) for r in db.execute(
            select(recipes).where(recipes.c.recipe_id.in_(ids))).mappings())
        ingredient_rows = tuple(dict(r) for r in db.execute(
            select(ingredients).where(ingredients.c.recipe_id.in_(ids))
            .order_by(ingredients.c.recipe_id, ingredients.c.sequence)).mappings())
    except (SQLAlchemyError, AttributeError):
        raise RecipeDataUnavailable() from None
    return RecipeRows(ids, recipe_rows, ingredient_rows)


def recipe_line_digest(recipe: Mapping, line: Mapping) -> str:
    """Bind the reviewed profile to all stored recipe and ingredient fields."""
    payload = json.dumps({"recipe": dict(recipe), "ingredient": dict(line)},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def prepare_database_recipe(rows: RecipeRows, *, policy: NutritionPolicy,
                            profiles: Mapping[str, RecipeLineProfile]) -> PreparedRecipe:
    issues: list[InputIssue] = []
    prepared: list[IngredientQuantity] = []
    recipes = {r["recipe_id"]: r for r in rows.recipes}
    seen_lines: set[str] = set()
    for rid in rows.recipe_ids:
        recipe = recipes.get(rid)
        if recipe is None:
            issues.append(InputIssue("recipe_not_found", rid))
            continue
        if recipe.get("review_status") != "approved" or recipe.get("is_eligible") is not True:
            issues.append(InputIssue("recipe_not_eligible_or_reviewed", rid))
        lines = [line for line in rows.ingredients if line["recipe_id"] == rid]
        if not lines:
            issues.append(InputIssue("recipe_has_no_ingredients", rid))
        for row in lines:
            lid = row["recipe_ingredient_id"]
            digest = recipe_line_digest(recipe, row)
            def issue(code: str, field: str | None = None) -> None:
                issues.append(InputIssue(code, rid, lid, field, digest))
            if not isinstance(lid, str) or not lid or lid in seen_lines:
                raise PlanningInputError("Recipe ingredient IDs must be unique.")
            seen_lines.add(lid)
            start = len(issues)
            def number(value, field: str, *, positive: bool = False):
                try:
                    result = exact(value)
                    if result < 0 or (positive and result == 0):
                        raise PlanningInputError("Invalid amount")
                    return result
                except PlanningInputError:
                    issue("missing_or_invalid_value", field)
                    return None
            servings = number(recipe.get("original_servings"), "original_servings", positive=True)
            required = row.get("is_required")
            if type(required) is not bool:
                issue("required_flag_unknown", "is_required")
            sample = policy.sample_for(lid, row["food_id"])
            if not isinstance(sample, str) or not sample:
                issue("tfda_mapping_missing", "sample_id")
            profile = profiles.get(lid)
            if profile is None:
                issue("reviewed_line_profile_missing", "profile")
                continue
            if (profile.review_status != "approved" or not profile.source or not profile.source_version
                    or profile.food_id != row["food_id"] or profile.input_digest != digest):
                issue("line_profile_unreviewed_or_stale", "profile")
                continue
            if profile.weight_basis not in ("purchase", "raw", "cooked"):
                issue("weight_basis_unknown", "weight_basis")
            if type(profile.consumed_as_is) is not bool:
                issue("consumed_as_is_unknown", "consumed_as_is")
            if policy.line_nutrition_records is not None:
                record = policy.line_nutrition_records.get(lid, {})
                if (record.get('state') == 'cooked' and profile.cooking_method is not None
                        and record.get('source', {}).get('cooking_method') != profile.cooking_method):
                    issue('imported_cooking_method_mismatch', 'cooking_method')
            step = number(profile.cooking_step_g, "cooking_step_g", positive=True)
            if profile.reviewed_weight_g is not None:
                weight_value = profile.reviewed_weight_g
            elif row.get("weight_status") == "source_explicit":
                weight_value = row.get("weight_g")
            else:
                weight_value = None
                issue("reviewed_gram_conversion_missing", "weight_g")
            weight = number(weight_value, "weight_g", positive=required is not False)
            minimum = maximum = None
            if row.get("adjustment_status") == "approved":
                if row.get("unit") == "g":
                    low, high = row.get("adjustable_min"), row.get("adjustable_max")
                else:
                    low, high = profile.reviewed_min_g, profile.reviewed_max_g
                minimum = number(low, "adjustable_min_g")
                maximum = number(high, "adjustable_max_g")
                if (minimum is not None and maximum is not None and weight is not None
                        and not minimum <= weight <= maximum):
                    issue("reviewed_range_invalid", "adjustable_range")
            evidence = policy.approved_evidence.get(profile.evidence_id) if profile.evidence_id else None
            if profile.evidence_id and evidence is None:
                issue("approved_cooking_evidence_missing", "evidence_id")
            if (not profile.consumed_as_is or profile.cooking_method) and evidence is None:
                issue("approved_cooking_evidence_missing", "evidence_id")
            if len(issues) != start:
                continue
            assert isinstance(sample, str) and weight is not None and isinstance(required, bool)
            assert servings is not None and step is not None
            prepared.append(IngredientQuantity(
                line_id=lid, food_id=row["food_id"], sample_id=sample, amount_g=weight,
                weight_basis=cast(Literal['purchase', 'raw', 'cooked'], profile.weight_basis),
                consumed_as_is=profile.consumed_as_is,
                cooking_step_g=step, cooking_method=profile.cooking_method, evidence=evidence,
                is_required=required, adjustment_status=row.get("adjustment_status", "not_reviewed"),
                adjustable_min_g=minimum, adjustable_max_g=maximum, original_servings=servings,
                cooking_context=dict(profile.cooking_context or {})))
    if issues:
        return PreparedRecipe(None, tuple(issues))
    return PreparedRecipe(RecipeCandidate("database_menu", tuple(prepared), rows.recipe_ids, "approved"), ())


def validate_database_menu(db: Connection, recipe_ids: Sequence[str], *, participations: list,
                           target_service, policy: NutritionPolicy,
                           profiles: Mapping[str, RecipeLineProfile],
                           t04_results: Mapping | None = None,
                           food_mapping_path=None,
                           nutrition_source: Literal['imported', 'legacy_tfda'] = 'imported',
                           nutrition_import_id: str | None = None,
                           nutrition_selections: Mapping | None = None) -> DatabaseMenuResult:
    """Backend participations → exact T03 targets → all DB lines → T12.

    Caller retains household authorization, T11 selection/fallback and T14 flow.
    No public preview or caller-supplied adoption flag is used as a target.
    """
    targets = targets_from_t03(target_service.aggregate_meal_targets(participations))
    rows = load_database_recipe_rows(db, recipe_ids)
    if nutrition_source == 'imported':
        if food_mapping_path is not None:
            raise PlanningInputError('Imported nutrition cannot use a legacy FOOD/TFDA registry.')
        from .imported_nutrition import load_imported_nutrition
        imported = load_imported_nutrition(db, rows, import_id=nutrition_import_id,
                                            selections=nutrition_selections or {})
        if imported.issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, imported.issues), None)
        policy = replace(policy, sample_mappings={}, line_nutrition_records=imported.line_records)
    elif nutrition_source == 'legacy_tfda':
        if nutrition_import_id is not None or nutrition_selections is not None:
            raise PlanningInputError('Legacy mode cannot consume imported nutrition selections.')
        policy = replace(policy, line_nutrition_records=None)
    else:
        raise PlanningInputError('Unknown nutrition source.')
    if food_mapping_path is not None:
        from nutrition_model.food_id_mapping import load_food_id_mappings
        names = {}
        for row in rows.ingredients:
            names.setdefault(row['food_id'], []).append(row.get('canonical_name'))
        try:
            mappings = load_food_id_mappings(food_mapping_path, policy.catalog_path, expected_names=names)
        except (OSError, ValueError, KeyError, TypeError):
            raise PlanningInputError('FOOD/TFDA mapping unavailable or incompatible.') from None
        if any(fid in policy.sample_mappings and policy.sample_mappings[fid] != sid
               for fid, sid in mappings.items()):
            raise PlanningInputError('Explicit FOOD/TFDA policy conflicts with the mapping registry.')
        policy = replace(policy, sample_mappings={**policy.sample_mappings, **mappings})
    if t04_results:
        from .t04_bridge import apply_t04_results
        policy, profiles, issues = apply_t04_results(rows, policy=policy, profiles=profiles,
                                                    results=t04_results)
        if issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, issues), None)
    preparation = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    result = validate_meal(preparation.recipe, targets, policy=policy) if preparation.recipe else None
    return DatabaseMenuResult(targets, preparation, result)
