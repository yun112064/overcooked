"""Hash-bound, opt-in continuation of reviewed T12 data in an isolated database.

This publishes a separate calculation policy. It never changes old SQL reviews,
uses recipe-derived nutrient aggregates, or contacts an external model.
"""
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import PROJECT_ROOT, settings
from app.models import RecipeDataRelease, NutritionImport, Recipe
from app.models.nutrition import NutritionFoodMapping
from app.services.nutrition_release import recipe_import_matches
from nutrition_planning.integration import (RecipeRows, InputIssue, load_database_recipe_rows,
                                           _brown_rice_policy, _supplement_record)
from nutrition_planning.imported_nutrition import ImportedNutritionData
from nutrition_planning.nutrition_mapping import load_nutrition_mappings
from nutrition_planning.nutrition_supplement import load_nutrition_supplements, NutritionSupplement
from nutrition_planning.reconciled_nutrition import resolve_reconciled_nutrition

REQUIRED_FILES = ('config/t12_nutrition_mappings.json', 'config/t12_nutrition_supplements.json',
                  'config/staples.json', 'config/t12_nutrient_fills.json',
                  'config/nutrition_as_is_categories.json')


class PolicyUnavailable(ValueError):
    pass


def enabled():
    return bool(getattr(settings, 'nutrition_policy_manifest', None))


def load_policy():
    try:
        raw = Path(settings.nutrition_policy_manifest).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if not settings.nutrition_policy_manifest_sha256 or digest != settings.nutrition_policy_manifest_sha256:
            raise PolicyUnavailable('nutrition_policy_manifest_hash_mismatch')
        policy = json.loads(raw.decode('utf-8-sig'))
        if (policy.get('status') != 'approved_for_isolated_acceptance'
                or policy.get('calculation_policy') != 'factor_one'
                or not policy.get('nutrition_policy_version')
                or policy.get('business_database') != 'meal_planner_1102_v3_1111_update'):
            raise PolicyUnavailable('nutrition_policy_not_approved')
        for field, configured in [('recipe_release_id', settings.generation_recipe_release_id),
                                  ('nutrition_import_id', settings.generation_nutrition_import_id),
                                  ('existing_food_mapping_version', settings.generation_food_mapping_version)]:
            if not configured or policy.get(field) != configured:
                raise PolicyUnavailable('nutrition_policy_runtime_version_mismatch')
        for relative in REQUIRED_FILES:
            if hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest() != policy['source_files'].get(relative):
                raise PolicyUnavailable('nutrition_policy_source_hash_mismatch')
        decision = policy['overlap_review_decision']
        accepted = set(decision['individually_accepted_food_ids'])
        approved = policy['approved_overlap_sources']
        if decision['blocked_food_ids'] or not accepted or accepted != {r['food_id'] for r in approved}:
            raise PolicyUnavailable('nutrition_policy_review_incomplete')
        if not policy.get('recipe_line_bindings') or not policy.get('recipe_bindings'):
            raise PolicyUnavailable('nutrition_policy_recipe_bindings_missing')
        return {**policy, '_sha256': digest}
    except PolicyUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyUnavailable('nutrition_policy_unavailable') from exc


def validate_database(db, policy):
    if db.bind.dialect.name == 'postgresql' and db.scalar(text('select current_database()')) != policy['business_database']:
        raise PolicyUnavailable('nutrition_policy_database_mismatch')
    release = db.get(RecipeDataRelease, policy['recipe_release_id'])
    imported = db.get(NutritionImport, policy['nutrition_import_id'])
    if (not release or not release.active or release.source_sha256 != policy['recipe_source_sha256']
            or not imported or not recipe_import_matches(db, release, imported,
                mapping_version=policy['existing_food_mapping_version'])):
        raise PolicyUnavailable('nutrition_policy_release_mismatch')


def _same_number(left, right):
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return False


def _bound_rows(rows, policy):
    for recipe in rows.recipes:
        if recipe['recipe_id'] == 'staple':
            continue
        bound = policy['recipe_bindings'].get(recipe['recipe_id'])
        if (not bound or recipe.get('recipe_release_id') != policy['recipe_release_id']
                or not _same_number(recipe.get('original_servings'), bound.get('original_servings'))
                or recipe.get('servings_basis') != bound.get('servings_basis')):
            raise PolicyUnavailable('nutrition_policy_recipe_changed')
    for row in rows.ingredients:
        if row['recipe_id'] == 'staple':
            if row['food_id'] not in ('FOOD000296', 'FOOD001487') or row['recipe_ingredient_id'] != 'staple:' + row['food_id']:
                raise PolicyUnavailable('nutrition_policy_staple_changed')
            continue
        bound = policy['recipe_line_bindings'].get(row['recipe_ingredient_id'])
        if (not bound or any(bound.get(k) != row.get(k) for k in ('recipe_id', 'food_id'))
                or not _same_number(bound.get('weight_g'), row.get('weight_g'))
                or (row.get('source_weight_g') is not None
                    and not _same_number(bound.get('weight_g'), row['source_weight_g']))):
            raise PolicyUnavailable('nutrition_policy_recipe_line_changed')


def brown_record(line):
    brown, issue = _brown_rice_policy('factor_one', 'imported')
    if issue or not brown:
        raise PolicyUnavailable('nutrition_policy_staple_changed')
    n = brown['nutrition']
    return _supplement_record(line, NutritionSupplement(
        brown['food_id'], brown['name'], 'values', n['state'], dict(n['values']), n['provider'],
        n['source_name'], n['source_record_id'], n['source_url'], n['retrieved_on'], n['basis'],
        brown['approved_by'], brown['approved_on'], brown['version']))


def resolve_rows(connection, rows, policy):
    """Both preview and formal generation use these identical source records."""
    _bound_rows(rows, policy)
    brown = {r['recipe_ingredient_id']: brown_record(r) for r in rows.ingredients
             if r['recipe_id'] == 'staple' and r['food_id'] == 'FOOD001487'}
    base = replace(rows, ingredients=tuple(r for r in rows.ingredients if r['recipe_ingredient_id'] not in brown))
    result = resolve_reconciled_nutrition(connection, base, import_id=policy['nutrition_import_id'],
        mappings=load_nutrition_mappings(), supplements=load_nutrition_supplements())
    records = dict(result.line_records)
    records.update(brown)
    approved = {(r['food_id'], r['nutrition_id'], r['nutrition_source_id']) for r in policy['approved_overlap_sources']}
    rejected = {(r.food_id, r.nutrition_id, r.source_id) for r in connection.execute(select(
        NutritionFoodMapping.food_id, NutritionFoodMapping.nutrition_id, NutritionFoodMapping.source_id).where(
            NutritionFoodMapping.recipe_release_id == policy['recipe_release_id'],
            NutritionFoodMapping.import_id == policy['nutrition_import_id'],
            NutritionFoodMapping.mapping_version == policy['existing_food_mapping_version'],
            NutritionFoodMapping.approval_status == 'rejected'))}
    lines = {r['recipe_ingredient_id']: r for r in rows.ingredients}
    issues = list(result.issues)
    accepted_foods = {r[0] for r in approved}
    for lid, record in list(records.items()):
        line, source = lines[lid], record['source']
        identity = (line['food_id'], source.get('nutrition_id'), source.get('nutrition_source_id'))
        # Reviewed foods pin an exact source, not permission to choose another
        # formerly rejected sample when its source records change.
        if ((identity in rejected or line['food_id'] in accepted_foods) and identity not in approved):
            issues.append(InputIssue('nutrition_policy_source_not_approved', line['recipe_id'], lid, 'nutrition'))
            del records[lid]
            continue
        records[lid] = {**record, 'source': {**source,
            'nutrition_policy_version': policy['nutrition_policy_version'],
            'nutrition_policy_manifest_sha256': policy['_sha256'],
            'recipe_release_id': policy['recipe_release_id']}}
    return ImportedNutritionData(records, tuple(issues))


def staple_table(policy=None):
    policy = policy or load_policy()
    config = json.loads((PROJECT_ROOT / 'config/staples.json').read_text(encoding='utf-8'))
    options = {}
    for key in ('white_rice', 'brown_rice'):
        row = config[key]
        options[row['name']] = {'food_id': row['food_id'], 'tfda_name': row['name'],
            'default_grams_per_n': row['grams_per_n'], 'adjustable_min': row['adjustable_min_g_per_n'],
            'adjustable_max': row['adjustable_max_g_per_n'], 'weight_basis': 'cooked',
            'source_cooking_method': row.get('source_cooking_method'), 'consumed_as_is': True,
            'nutrition_provider': 'TFDA' if key == 'white_rice' else 'USDA'}
    return {'version': policy['nutrition_policy_version'], 'review_status': 'user_confirmed',
            'none_values': ['不需要主食', '無主食', '沒有主食'], 'options': options}


def make_calculator(factory):
    """One job, one immutable source snapshot. New/managed recipes need rebinding."""
    from app.services.meal_calculation import IngredientNutrition
    from nutrition_planning.integration import ALL_NUTRIENTS
    records = None
    errors = {}
    line_foods = {}
    policy_error = None
    pinned_release = None

    def calculate(pairs):
        nonlocal records, policy_error, pinned_release
        if records is None:
            records = {}
            try:
                policy = load_policy()
                pinned_release = policy['recipe_release_id']
                with factory() as db:
                    validate_database(db, policy)
                    ids = list(db.scalars(select(Recipe.recipe_id).where(Recipe.recipe_release_id == policy['recipe_release_id'])))
                    rows = load_database_recipe_rows(db.connection(), ids)
                    staple_lines = tuple({'recipe_id': 'staple', 'recipe_ingredient_id': 'staple:' + fid,
                        'food_id': fid, 'canonical_name': name, 'weight_g': Decimal('160'), 'unit': 'g',
                        'sequence': number, 'is_required': True, 'weight_status': 'source_explicit'}
                        for number, (fid, name) in enumerate([('FOOD000296', '白飯'), ('FOOD001487', '糙米飯')], 1))
                    rows = replace(rows, recipes=rows.recipes + ({'recipe_id': 'staple', 'original_servings': 1,
                        'review_status': 'approved', 'is_eligible': True},), ingredients=rows.ingredients + staple_lines)
                    result = resolve_rows(db.connection(), rows, policy)
                    records.update(result.line_records)
                    line_foods.update({r['recipe_ingredient_id']: r['food_id'] for r in rows.ingredients})
                    errors.update({i.line_id: i.code for i in result.issues})
            except PolicyUnavailable as exc:
                policy_error = str(exc)
        results = []
        for line, grams in pairs:
            lid = 'staple:' + line.food_id if line.role == 'staple' else line.recipe_ingredient_id
            code = policy_error or errors.get(lid)
            record = records.get(lid)
            if not code and (not record or line_foods.get(lid) != line.food_id or line.recipe_publication_id):
                code = 'nutrition_policy_line_not_bound'
            if not code and line.recipe_release_id not in (None, pinned_release):
                code = 'nutrition_policy_recipe_changed'
            if code:
                results.append(IngredientNutrition({k: None for k in ALL_NUTRIENTS},
                    {'missing_reason': code, 'nutrition_basis': 'reconciled_factor_one'}, code))
                continue
            values = {key: (None if record['nutrition_per_100g'].get(key) is None else
                Fraction(record['nutrition_per_100g'][key]) * Fraction(grams) / 100) for key in ALL_NUTRIENTS}
            values = {key: None if val is None else Decimal(val.numerator) / Decimal(val.denominator)
                      for key, val in values.items()}
            results.append(IngredientNutrition(values, {**record['source'], 'weight_basis': record['state'],
                'nutrition_basis': 'reconciled_factor_one', 'is_estimated': False,
                'cooking_conversion_applied': False}, None))
        return results
    return calculate
