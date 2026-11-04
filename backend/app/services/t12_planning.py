"""Trusted backend bridge: stored questionnaire → T03 → T12."""
from pathlib import Path

from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.services import household, meal_request
from app.models import RecipeDataRelease, Recipe, NutritionImport
from app.services.nutrition_release import recipe_import_matches
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
    if not settings.generation_nutrition_import_id:
        raise T12PlanningUnavailable(
            'NUTRITION_VERSION_REQUIRED', '營養資料版本尚未確認，請先完成資料來源整合。')
    try:
        recipe_release_id = getattr(settings, 'generation_recipe_release_id', None)
        if recipe_release_id:
            release = db.get(RecipeDataRelease, recipe_release_id)
            imported = db.get(NutritionImport, settings.generation_nutrition_import_id)
            if (not release or not imported or not recipe_import_matches(db, release, imported,
                    mapping_version=getattr(settings, 'generation_food_mapping_version', None))):
                raise T12PlanningUnavailable('RECIPE_NUTRITION_RELEASE_MISMATCH', '食譜與營養資料發布版本不相符。')
            selected = list(db.execute(select(Recipe.recipe_id, Recipe.recipe_release_id).where(
                Recipe.recipe_id.in_(recipe_ids))))
            if (set(r.recipe_id for r in selected) != set(recipe_ids)
                    or any(r.recipe_release_id != recipe_release_id for r in selected)):
                raise T12PlanningUnavailable('RECIPE_NUTRITION_RELEASE_MISMATCH', '所選食譜不屬於目前的營養資料發布。')
        result = validate_database_menu(
            db.connection(), recipe_ids, participations=meal_request.target_participations(draft, members),
            target_service=household.target_service(),
            # Imported Food_ID records replace the catalog path; this placeholder
            # is never read in imported mode.
            policy=NutritionPolicy(Path(settings.nutrition_rules_path), {}, ('energy_kcal', 'protein_g')),
            profiles=None, staple=draft.staple, nutrition_source='imported',
            nutrition_import_id=settings.generation_nutrition_import_id, nutrition_selections=None,
            conversion=settings.t12_preview_conversion)
    except (RecipeDataUnavailable, NutritionDataUnavailable) as exc:
        raise T12PlanningUnavailable(exc.code, '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    except SQLAlchemyError:
        raise T12PlanningUnavailable('NUTRITION_DATA_UNAVAILABLE', '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    return result.to_dict()
