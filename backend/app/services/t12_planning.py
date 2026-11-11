"""Trusted backend bridge: stored questionnaire → T03 → T12."""
from pathlib import Path

from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.management.active_recipe_catalog import RECIPE_PREFIX, active_catalog, planning_rows
from app.services import household, meal_request, per_person_nutrition
from app.services.legacy_t12_preview import _with_spoons
from app.services.recipe_volume import attach_preview_volumes
from app.models import RecipeDataRelease, Recipe, RecipeIngredient, NutritionImport
from app.models.nutrition import NutritionFoodMapping
from app.services.nutrition_release import recipe_import_matches
from app.services import reconciled_policy
from nutrition_planning import (NutritionPolicy, NutritionDataUnavailable,
                                RecipeDataUnavailable, validate_database_menu)


class T12PlanningUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def preview(db: Session, *, draft: meal_request.MealRequestDraft, members: dict,
            recipe_ids: list[str]) -> dict:
    """Calculate with only server-side questionnaire, recipe and nutrition data."""
    try:
        continued_policy = reconciled_policy.load_policy() if reconciled_policy.enabled() else None
        if continued_policy:
            reconciled_policy.validate_database(db, continued_policy)
        catalog = active_catalog(db)
    except reconciled_policy.PolicyUnavailable as exc:
        raise T12PlanningUnavailable('NUTRITION_POLICY_UNAVAILABLE', str(exc)) from None
    except SQLAlchemyError:
        raise T12PlanningUnavailable('RECIPE_DATA_UNAVAILABLE', '目前食譜發布無法讀取，請稍後重試。') from None
    managed_ids = {rid for rid in recipe_ids if rid.startswith(RECIPE_PREFIX)}
    if ((managed_ids and (not catalog or not managed_ids <= catalog.recipes.keys()))
            or (catalog and not (set(recipe_ids) - managed_ids) <= catalog.legacy_ids)):
        raise T12PlanningUnavailable('RECIPE_PUBLICATION_CHANGED', '食譜版本已切換，請重新查詢菜單。')
    if (hasattr(settings, 'generation_recipe_release_id')
            and not settings.generation_recipe_release_id
            and not settings.generation_nutrition_import_id and not managed_ids):
        from app.services.legacy_t12_preview import preview as legacy_preview
        return legacy_preview(db, draft=draft, members=members, recipe_ids=recipe_ids)
    if not settings.generation_nutrition_import_id:
        raise T12PlanningUnavailable(
            'NUTRITION_VERSION_REQUIRED', '營養資料版本尚未確認，請先完成資料來源整合。')
    try:
        recipe_release_id = getattr(settings, 'generation_recipe_release_id', None) or (
            catalog.base_release_id if managed_ids else None)
        rows = planning_rows(db, catalog, recipe_ids) if managed_ids else None
        if recipe_release_id:
            release = db.get(RecipeDataRelease, recipe_release_id)
            imported = db.get(NutritionImport, settings.generation_nutrition_import_id)
            if (not release or not imported or not recipe_import_matches(db, release, imported,
                    mapping_version=getattr(settings, 'generation_food_mapping_version', None))):
                raise T12PlanningUnavailable('RECIPE_NUTRITION_RELEASE_MISMATCH', '食譜與營養資料發布版本不相符。')
            legacy_ids = set(recipe_ids) - managed_ids
            selected = list(db.execute(select(Recipe.recipe_id, Recipe.recipe_release_id).where(
                Recipe.recipe_id.in_(legacy_ids))))
            if (set(r.recipe_id for r in selected) != legacy_ids
                    or any(r.recipe_release_id != recipe_release_id for r in selected)):
                raise T12PlanningUnavailable('RECIPE_NUTRITION_RELEASE_MISMATCH', '所選食譜不屬於目前的營養資料發布。')
        result = validate_database_menu(
            db.connection(), recipe_ids, **({'rows': rows} if rows else {}),
            participations=meal_request.target_participations(draft, members),
            target_service=household.target_service(),
            # Imported Food_ID records replace the catalog path; this placeholder
            # is never read in imported mode.
            policy=NutritionPolicy(Path(settings.nutrition_rules_path), {}, ('energy_kcal', 'protein_g'),
                                   approved_for_adoption=bool(continued_policy)),
            profiles=None, staple=draft.staple, nutrition_source='imported',
            nutrition_import_id=settings.generation_nutrition_import_id, nutrition_selections=None,
            conversion='factor_one' if continued_policy else settings.t12_preview_conversion,
            nutrition_resolver=(lambda connection, recipe_rows: reconciled_policy.resolve_rows(
                connection, recipe_rows, continued_policy)) if continued_policy else None)
    except reconciled_policy.PolicyUnavailable as exc:
        raise T12PlanningUnavailable('NUTRITION_POLICY_UNAVAILABLE', str(exc)) from None
    except (RecipeDataUnavailable, NutritionDataUnavailable) as exc:
        raise T12PlanningUnavailable(exc.code, '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    except SQLAlchemyError:
        raise T12PlanningUnavailable('NUTRITION_DATA_UNAVAILABLE', '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    payload = dict(result.to_dict())
    attach_preview_volumes(db, result, payload)
    if recipe_release_id and not continued_policy:
        # Main's unversioned preview policies never approve a V3 mapping.
        approved = set(db.scalars(select(NutritionFoodMapping.food_id).where(
            NutritionFoodMapping.recipe_release_id == recipe_release_id,
            NutritionFoodMapping.import_id == settings.generation_nutrition_import_id,
            NutritionFoodMapping.mapping_version == settings.generation_food_mapping_version,
            NutritionFoodMapping.approval_status == 'approved')))
        lines = rows.ingredients if rows else db.execute(select(
            RecipeIngredient.recipe_id, RecipeIngredient.recipe_ingredient_id,
            RecipeIngredient.food_id).where(RecipeIngredient.recipe_id.in_(recipe_ids))).mappings()
        pending = [{'code': 'nutrition_mapping_pending_review', 'recipe_id': r['recipe_id'],
                    'line_id': r['recipe_ingredient_id'], 'message': 'V3 營養對照尚未核准，請先完成 T04 審查。',
                    'adoption_blocked': True} for r in lines if r['food_id'] not in approved]
        if pending:
            payload.update(status='calculation_incomplete', status_message='V3 營養對照尚未核准',
                           nutrition_passed=False, adoption_blocked=True, final_food_ids=(),
                           per_person=(), nutrition_all=None,
                           input_issues=tuple(payload.get('input_issues') or ()) + tuple(pending))
            payload['nutrition'] = {'totals': None, 'missing': [r['line_id'] for r in pending]}

    if 'dishes' not in payload:
        return payload
    payload['dishes'] = [{**dish, 'ingredients': [_with_spoons(i) for i in dish['ingredients']]}
                         for dish in payload.get('dishes') or ()]
    names = {str(mid): m.display_name for mid, m in members.items()}
    payload['people'] = per_person_nutrition.people(list(payload.get('per_person') or ()), names)
    payload['daily_reference_meta'] = per_person_nutrition.reference_meta()
    return payload
