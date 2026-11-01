"""Trusted backend bridge: stored questionnaire → T03 → T12."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import household, meal_request
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
        result = validate_database_menu(
            db.connection(), recipe_ids, participations=meal_request.target_participations(draft, members),
            target_service=household.target_service(),
            # Imported Food_ID records replace the catalog path; this placeholder
            # is never read in imported mode.
            policy=NutritionPolicy(Path(settings.nutrition_rules_path), {}, ('energy_kcal', 'protein_g')),
            profiles=None, staple=draft.staple, nutrition_source='imported',
            nutrition_import_id=None, nutrition_selections=None)
    except (RecipeDataUnavailable, NutritionDataUnavailable) as exc:
        raise T12PlanningUnavailable(exc.code, '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    return result.to_dict()
