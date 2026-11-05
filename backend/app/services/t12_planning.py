"""Trusted backend bridge: stored questionnaire → T03 → T12."""
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import household, meal_request, per_person_nutrition
from app.services.seasonings import display_group, spoon_display
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
            # 2026-11-04: the T04 nutrition policy is approved for adoption (user decision).
            policy=NutritionPolicy(Path(settings.nutrition_rules_path), {}, ('energy_kcal', 'protein_g'),
                                   approved_for_adoption=True),
            profiles=None, staple=draft.staple, nutrition_source='imported',
            nutrition_import_id=None, nutrition_selections=None,
            # 2026-11-01: recipe grams × raw composition, factor 1 for
            # energy/protein; TFDA first, labelled USDA fallback.
            conversion='factor_one')
    except (RecipeDataUnavailable, NutritionDataUnavailable) as exc:
        raise T12PlanningUnavailable(exc.code, '營養或食譜資料暫時無法讀取，請稍後重試。') from None
    payload: dict[str, Any] = dict(result.to_dict())
    payload['dishes'] = [{**dish, 'ingredients': [_with_spoons(i) for i in dish['ingredients']]}
                         for dish in payload.get('dishes') or ()]
    # 每人（用餐與便當分開）對照每日建議攝取量，畫面用
    names = {str(member_id): member.display_name for member_id, member in members.items()}
    payload['people'] = per_person_nutrition.people(list(payload.get('per_person') or ()), names)
    payload['daily_reference_meta'] = per_person_nutrition.reference_meta()
    return payload


def _with_spoons(ingredient: dict) -> dict:
    """調味料改用小匙／大匙顯示（設定檔 config/seasoning_ingredients.json）；只影響畫面，計算仍用克數。"""
    grams = Fraction(ingredient['grams']) if ingredient.get('grams') is not None else None
    shown = (spoon_display(ingredient['food_id'], Decimal(grams.numerator) / Decimal(grams.denominator),
                           ingredient.get('unit'))
             if ingredient.get('food_id') and grams is not None else None)
    return {**ingredient, 'group': display_group(ingredient.get('food_id')),
            **(shown or {'display_amount': None, 'display_unit': None, 'display_text': None})}
