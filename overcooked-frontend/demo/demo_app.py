"""Real API/session/persistence with explicitly synthetic nutrition for UI demonstration.

This module is never imported by production. It does not approve or import real
nutrition observations. The isolated container network has no Internet egress.
"""
import os
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import select
from app.core.config import settings
from app.db.session import engine

if os.environ.get('DEMO_DATABASE_ONLY') != 'true' or engine.url.database != 'meal_planner_1102_v3_ui_demo_test':
    raise RuntimeError('The demonstration adapter requires its dedicated database')
if settings.openai_api_key or os.environ.get('GEMINI_API_KEY'):
    raise RuntimeError('External model keys are prohibited in the demonstration environment')

from app.services import t12_planning, meal_request, per_person_nutrition
from app.models import Recipe, RecipeIngredient, RecipeStep

def demo_preview(db, *, draft, members, recipe_ids):
    target = meal_request.target_preview(draft, members)
    if target.get('status') != 'available':
        raise t12_planning.T12PlanningUnavailable('DEMO_TARGET_INCOMPLETE', '請先完成示範成員與用餐條件。')
    n = Decimal(target['total_n'])
    totals = {'energy_kcal': target['target']['energy_kcal'], 'protein_g': target['target']['protein_g'],
              **{key: None for key in ['fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg', 'dietary_fiber_g']}}
    dishes = []
    for rid in recipe_ids:
        recipe = db.get(Recipe, rid)
        if recipe is None:
            raise t12_planning.T12PlanningUnavailable('DEMO_RECIPE_NOT_FOUND', '找不到此示範食譜。')
        ingredients = []
        for line in db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id == rid).order_by(RecipeIngredient.sequence)):
            grams = line.weight_g / recipe.original_servings * n if line.weight_g is not None else None
            ingredients.append({'name': line.canonical_name, 'food_id': line.food_id, 'unit': line.unit,
                'grams': None if grams is None else str(grams), 'line_id': line.recipe_ingredient_id,
                'display_amount': None, 'display_unit': None, 'display_text': None})
        steps = db.scalars(select(RecipeStep.instruction_clean).where(RecipeStep.recipe_id == rid).order_by(RecipeStep.step_no)).all()
        dishes.append({'recipe_id': rid, 'ingredients': ingredients, 'steps': list(steps), 'adjusted': False})
    people = []
    energy = Decimal(totals['energy_kcal'])
    for item in target['items']:
        scaled = item['scaled_target']
        share = Decimal(scaled['energy_kcal']) / energy
        member_id = item['participation_id'].split(':')[0]
        intake = {k: str(Decimal(v) * share) if v is not None else None for k, v in totals.items()}
        people.append({'participation_id': item['participation_id'], 'member_id': member_id,
            'kind': item['kind'], 'meal': item['meal'], 'n': str(item['n']), 'dish_share': str(share),
            'share': str(share), 'meal_ratio': str(item['meal_ratio']), 'staple_g': None, 'dish_ids': recipe_ids,
            'daily': {key: str(Decimal(scaled[key]) / Decimal(str(item['meal_ratio'])) / Decimal(str(item['n']))) for key in ['energy_kcal', 'protein_g']},
            'target': scaled, 'nutrition': {k: intake[k] for k in ['energy_kcal', 'protein_g']},
            'nutrition_all': intake, 'intake': intake, 'known_partial': {},
            'within_10pct': {'energy_kcal': True, 'protein_g': True}})
    missing = {k: ['示範環境未提供此營養資料'] for k, v in totals.items() if v is None}
    return {'status': 'nutrition_passed', 'status_message': '介面展示的合成數值，不是實際食譜營養驗證。',
        'dish_portions': {'factors': {rid: str(n) for rid in recipe_ids}}, 'dishes': dishes, 'staple': None,
        'per_person': people, 'targets': target['target'], 'input_issues': [], 'adoption_blocked': False,
        'final_food_ids': list({i['food_id'] for d in dishes for i in d['ingredients']}), 'nutrition_passed': True,
        'nutrition': {'totals': {k: totals[k] for k in ['energy_kcal', 'protein_g']}, 'missing': []},
        'nutrition_all': {'totals': totals, 'known_partial': {}, 'missing_by_nutrient': missing},
        'people': per_person_nutrition.people(people, {str(k): v.display_name for k, v in members.items()}),
        'daily_reference_meta': per_person_nutrition.reference_meta()}

t12_planning.preview = demo_preview
from app.main import app

@app.middleware('http')
async def mark_demo(request, call_next):
    response = await call_next(request)
    response.headers['X-Overcooked-Environment'] = 'synthetic-ui-demo'
    return response
