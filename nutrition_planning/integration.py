"""Trusted backend bridge from T01/T11 recipe IDs and T03/T04 to T12.

No HTTP, adoption, RAG, or generation orchestration. Profiles and NutritionPolicy
must come from the backend's reviewed data, never from browser/AI assertions.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

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
    steps: tuple[Mapping, ...] = ()


@dataclass(frozen=True)
class InputIssue:
    code: str
    recipe_id: str
    line_id: str | None = None
    field: str | None = None
    input_digest: str | None = None

    @property
    def message(self) -> str:
        labels = {
            'nutrition_selection_missing': '缺少營養資料選擇',
            'nutrition_record_not_found_for_import_and_food': '食材營養資料缺失',
            'nutrition_food_id_missing': '食材營養資料缺失',
            'nutrition_variant_decision_required': '烹調方式有多種營養資料，等待 LLM 判斷',
            'llm_variant_decision_invalid': 'LLM 烹調方式判斷格式無效',
            'llm_variant_decision_mismatch': 'LLM 烹調方式判斷與 Food_ID 或營養資料不一致',
            'nutrition_variant_mismatch': '生熟或烹調方式不符合',
            'nutrition_state_unknown': '烹調生熟狀態無法判定',
            'tfda_source_missing_or_ambiguous': 'TFDA 營養來源無法唯一判定',
            'staple_nutrition_selection_missing': '主食份量／營養依據缺值',
            'staple_tfda_method_mismatch': '主食烹調方式與營養樣品不一致',
            'staple_tfda_sample_mismatch': '主食 TFDA 樣品不一致',
            'staple_policy_unavailable': '主食份量／營養依據缺值',
            'llm_raw_energy_missing': '缺少生食熱量基礎資料',
            'llm_raw_protein_missing': '缺少生食蛋白質基礎資料',
            'llm_not_configured': 'LLM 服務未設定',
            'llm_provider_error': 'LLM 服務錯誤',
            'cooked_weight_or_nutrients_unavailable': '缺少熟食產率或營養保留率',
            'llm_estimation_failed': 'LLM 熟食估算失敗',
            'cooking_method_unavailable': '烹調方式無法判定',
            't04_import_snapshot_mismatch': 'LLM 估算與營養資料版本不一致',
            't04_parameters_missing_invalid_or_sample_mismatch': 'LLM 烹調參數缺失或與食材樣品不一致',
            'approved_cooking_evidence_missing': '缺少已驗證的烹調依據',
            'llm_service_unavailable': 'LLM 服務無法使用',
            'llm_weight_missing': '缺少烹調前重量',
        }
        return labels.get(self.code, '營養或烹調依據缺失，無法採用')

    @property
    def adoption_blocked(self) -> bool:
        return True


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
    staple_snapshot: Mapping | None = None

    @property
    def input_issues(self) -> tuple[Mapping[str, object], ...]:
        issues = self.preparation.issues
        return tuple({'code': issue.code, 'recipe_id': issue.recipe_id,
                      'line_id': issue.line_id, 'field': issue.field,
                      'message': issue.message, 'adoption_blocked': issue.adoption_blocked}
                     for issue in issues)

    @property
    def adoption_blocked(self) -> bool:
        return bool(self.preparation.issues or self.validation is None
                    or not self.validation.nutrition_passed
                    or self.validation.adoption_blockers)

    @property
    def final_food_ids(self) -> tuple[str, ...]:
        """Only validated final ingredients, for the caller's later RAG step."""
        if not self.validation or not self.validation.nutrition_passed or not self.validation.final_recipe:
            return ()
        return tuple(sorted({line.food_id for line in self.validation.final_recipe.ingredients
                             if exact(line.amount_g) > 0}))


def estimate_missing_cooking_with_t04(rows: RecipeRows, *, profiles: Mapping[str, RecipeLineProfile],
                                      line_records: Mapping[str, Mapping], estimator) -> tuple[Mapping[str, object], tuple[InputIssue, ...]]:
    """Run the existing T04 estimator only for selected raw records without cooked data.

    The estimator receives TFDA raw values and returns parameters only. T12 still
    performs all nutrition arithmetic and rejects incomplete receipts.
    """
    try:
        from cooked_nutrition_service import calculate_cooking  # type: ignore[import-not-found]
    except ImportError:
        return {}, (InputIssue('llm_service_unavailable', '', field='estimator'),)
    results: dict[str, object] = {}
    issues: list[InputIssue] = []
    for line in rows.ingredients:
        lid = line['recipe_ingredient_id']
        record = line_records.get(lid)
        profile = profiles.get(lid)
        if not isinstance(record, Mapping) or not isinstance(profile, RecipeLineProfile):
            continue
        if record.get('state') != 'raw':
            continue
        if not profile.cooking_method:
            issues.append(InputIssue('cooking_method_unavailable', line['recipe_id'], lid, 'cooking_method'))
            continue
        if estimator is None:
            try:
                from llm_cooking_estimator import estimate_cooking  # type: ignore[import-not-found]
                estimator = estimate_cooking
            except ImportError:
                issues.append(InputIssue('llm_service_unavailable', line['recipe_id'], lid, 'estimator'))
                continue
        raw_source = record.get('source')
        raw_values = {key: float(value) if value is not None else None
                      for key, value in dict(record.get('nutrition_per_100g') or {}).items()}
        raw = {'ingredient_id': record.get('ingredient_id'),
               'standard_name': record.get('standard_name'), 'state': 'raw',
               'nutrition_per_100g': raw_values,
               'source': dict(raw_source) if isinstance(raw_source, Mapping) else {}}
        if raw['nutrition_per_100g'].get('energy_kcal') is None:
            issues.append(InputIssue('llm_raw_energy_missing', line['recipe_id'], lid, 'energy_kcal'))
            continue
        if raw['nutrition_per_100g'].get('protein_g') is None:
            issues.append(InputIssue('llm_raw_protein_missing', line['recipe_id'], lid, 'protein_g'))
            continue
        try:
            weight = line.get('weight_g')
            if not isinstance(weight, (int, float, Decimal)) or isinstance(weight, bool):
                issues.append(InputIssue('llm_weight_missing', line['recipe_id'], lid, 'weight_g'))
                continue
            result = calculate_cooking(raw['standard_name'] or profile.food_id, float(weight), profile.cooking_method,
                                       [raw], lambda _: raw, cooking_context=profile.cooking_context or {},
                                       estimator=estimator)
        except (TypeError, ValueError, KeyError, OSError, TimeoutError):
            issues.append(InputIssue('llm_estimation_failed', line['recipe_id'], lid, 'cooking'))
            continue
        if not result.get('cooking_adjustment_complete'):
            issues.append(InputIssue(result.get('missing_reason', 'llm_estimation_failed'),
                                     line['recipe_id'], lid, 'cooking'))
            continue
        results[lid] = result
    return results, tuple(issues)


def load_database_recipe_rows(db: Connection, recipe_ids: Sequence[str]) -> RecipeRows:
    if (isinstance(recipe_ids, (str, bytes)) or not recipe_ids
            or any(not isinstance(i, str) or not i.strip() for i in recipe_ids)
            or len(set(recipe_ids)) != len(recipe_ids)):
        raise PlanningInputError("Provide distinct recipe IDs selected by T11.")
    ids = tuple(recipe_ids)
    try:
        metadata = MetaData()
        recipes = Table("recipes", metadata, autoload_with=db, resolve_fks=False)
        ingredients = Table("recipe_ingredients", metadata, autoload_with=db, resolve_fks=False)
        # Read every line: available ingredients, oil and seasonings included.
        recipe_rows = tuple(dict(r) for r in db.execute(
            select(recipes).where(recipes.c.recipe_id.in_(ids))).mappings())
        ingredient_rows = tuple(dict(r) for r in db.execute(
            select(ingredients).where(ingredients.c.recipe_id.in_(ids))
            .order_by(ingredients.c.recipe_id, ingredients.c.sequence)).mappings())
        try:
            steps = Table("recipe_steps", metadata, autoload_with=db, resolve_fks=False)
            step_rows = tuple(dict(r) for r in db.execute(
                select(steps).where(steps.c.recipe_id.in_(ids)).order_by(steps.c.recipe_id, steps.c.step_no)
            ).mappings())
        except (SQLAlchemyError, AttributeError):
            step_rows = ()
    except (SQLAlchemyError, AttributeError):
        raise RecipeDataUnavailable() from None
    return RecipeRows(ids, recipe_rows, ingredient_rows, step_rows)


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
                           staple: str | None = None,
                           t04_results: Mapping | None = None,
                           food_mapping_path=None,
                           nutrition_source: Literal['imported', 'legacy_tfda'] = 'imported',
                           nutrition_import_id: str | None = None,
                           nutrition_selections: Mapping | None = None,
                           cooking_estimator=None, variant_resolver=None) -> DatabaseMenuResult:
    """Backend participations → exact T03 targets → all DB lines → T12.

    Caller retains household authorization, T11 selection/fallback and T14 flow.
    No public preview or caller-supplied adoption flag is used as a target.
    """
    targets = targets_from_t03(target_service.aggregate_meal_targets(participations))
    rows = load_database_recipe_rows(db, recipe_ids)
    if staple is None:
        return DatabaseMenuResult(targets, PreparedRecipe(None, (
            InputIssue('staple_selection_required', 'staple', field='staple'),)), None)
    staple_snapshot = None
    if staple == '白飯':
        try:
            config = json.loads((Path(__file__).resolve().parents[1] / 'config/staples.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('staple_policy_unavailable', 'staple', field='policy'),)), None)
        expected_rice = {'name': '白飯', 'food_id': 'FOOD000296', 'tfda_sample_id': 'A0550601',
                         'weight_basis': 'cooked', 'source_cooking_method': '電鍋煮',
                         'grams_per_n': '160',
                         'adjustable_min_g_per_n': '100', 'adjustable_max_g_per_n': '250',
                         'cooking_step_g': '1'}
        if (not isinstance(config, dict) or config.get('version') != 'staples-2026-11-01-v2'
                or config.get('review_status') != 'user_confirmed'
                or config.get('white_rice') != expected_rice):
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('staple_policy_unreviewed_or_changed', 'staple', field='policy'),)), None)
        rice = expected_rice
        staple_snapshot = {'version': config['version'], 'review_status': config['review_status'],
                           **rice, 'selection': staple}
        lid = 'staple:FOOD000296'
        recipe_row = {'recipe_id': 'staple', 'original_servings': 1,
                      'review_status': 'approved', 'is_eligible': True}
        line_row = {'recipe_ingredient_id': lid, 'recipe_id': 'staple', 'sequence': 1,
                    'food_id': rice['food_id'], 'canonical_name': rice['name'],
                    'weight_g': rice['grams_per_n'], 'weight_status': 'source_explicit',
                    'unit': 'g', 'is_required': True, 'adjustment_status': 'approved',
                    'adjustable_min': rice['adjustable_min_g_per_n'],
                    'adjustable_max': rice['adjustable_max_g_per_n']}
        rows = RecipeRows(rows.recipe_ids + ('staple',), rows.recipes + (recipe_row,),
                          rows.ingredients + (line_row,), rows.steps)
        profiles = {**profiles, lid: RecipeLineProfile(
            rice['food_id'], recipe_line_digest(recipe_row, line_row), 'cooked', True,
            rice['cooking_step_g'], 'user-confirmed-staple', config['version'],
            review_status='approved')}
        if nutrition_source == 'legacy_tfda':
            existing_sample = policy.sample_mappings.get(rice['food_id'])
            if existing_sample is not None and existing_sample != rice['tfda_sample_id']:
                return DatabaseMenuResult(targets, PreparedRecipe(None, (
                    InputIssue('staple_sample_mapping_conflict', 'staple', lid, 'sample_id'),)), None)
            policy = replace(policy, sample_mappings={**policy.sample_mappings,
                rice['food_id']: rice['tfda_sample_id']})
    elif staple not in ('不需要主食', '無主食', '沒有主食'):
        return DatabaseMenuResult(targets, PreparedRecipe(None, (
            InputIssue('staple_not_supported', 'staple', field='staple'),)), None)
    if nutrition_source == 'imported':
        if food_mapping_path is not None:
            raise PlanningInputError('Imported nutrition cannot use a legacy FOOD/TFDA registry.')
        from .imported_nutrition import load_imported_nutrition, resolve_imported_selections
        resolved_selections, selection_issues = resolve_imported_selections(
            db, rows, import_id=nutrition_import_id, selections=nutrition_selections,
            profiles=profiles, variant_resolver=variant_resolver)
        if selection_issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, selection_issues), None)
        imported = load_imported_nutrition(db, rows, import_id=nutrition_import_id,
                                            selections=resolved_selections)
        if imported.issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, imported.issues), None)
        policy = replace(policy, sample_mappings={}, line_nutrition_records=imported.line_records)
        estimates, estimate_issues = ({}, ())
        if t04_results is None and cooking_estimator is not None:
            estimates, estimate_issues = estimate_missing_cooking_with_t04(
                rows, profiles=profiles, line_records=imported.line_records, estimator=cooking_estimator)
        if estimate_issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, estimate_issues), None)
        if estimates:
            from .t04_bridge import T04CookingResult, apply_t04_results
            receipts = {}
            for lid, estimate in estimates.items():
                line = next(row for row in rows.ingredients if row['recipe_ingredient_id'] == lid)
                profile = profiles[lid]
                source = imported.line_records[lid]['source']
                receipts[lid] = T04CookingResult(
                    profile.input_digest, profile.food_id, profile.weight_basis,
                    str(line.get('weight_g')), profile.cooking_method or '',
                    dict(profile.cooking_context or {}), 'T04-auto-estimator', cast(Any, estimate),
                    source.get('import_id'), source.get('nutrition_id'), source.get('nutrition_source_id'))
            t04_policy, profiles, t04_issues = apply_t04_results(
                rows, policy=policy, profiles=profiles, results=receipts)
            if t04_issues:
                return DatabaseMenuResult(targets, PreparedRecipe(None, t04_issues), None)
            policy = t04_policy
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
    if staple == '白飯' and preparation.recipe is not None:
        preparation = PreparedRecipe(replace(preparation.recipe, recipe_ids=tuple(recipe_ids)), ())
    result = validate_meal(preparation.recipe, targets, policy=policy) if preparation.recipe else None
    if staple_snapshot is not None and result and result.final_recipe:
        rice_line = next((line for line in result.final_recipe.ingredients
                          if line.line_id == 'staple:FOOD000296'), None)
        if rice_line is not None:
            staple_snapshot = {**staple_snapshot, 'final_grams': str(rice_line.amount_g),
                               'total_n': str(targets.total_n)}
    return DatabaseMenuResult(targets, preparation, result, staple_snapshot)
