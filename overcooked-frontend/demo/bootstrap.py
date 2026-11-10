"""Only provisions a new, explicitly named synthetic demonstration database."""
from datetime import date, timedelta
from decimal import Decimal
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from app.core.config import settings
from app.db.session import engine, SessionLocal

EXPECTED = 'meal_planner_1102_v3_ui_demo_test'
if os.environ.get('DEMO_DATABASE_ONLY') != 'true' or engine.url.database != EXPECTED or engine.url.host != 'db':
    raise RuntimeError('Refusing to initialize anything other than the isolated demo database')
with engine.begin() as c:
    name = c.scalar(text('SELECT current_database()'))
    marker = c.scalar(text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"))
    count = c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'"))
    if name != EXPECTED or (count and marker != 'overcooked:disposable:ui-demo'):
        raise RuntimeError('Existing database is not marked as this demonstration environment')
    c.execute(text("COMMENT ON DATABASE meal_planner_1102_v3_ui_demo_test IS 'overcooked:disposable:ui-demo'"))
cfg = Config('/app/alembic.ini')
cfg.set_main_option('script_location', '/app/migrations')
command.upgrade(cfg, 'head')

from app.db.models import User, Household, HouseholdMembership, Member, MealRequest, MealRequestRevision
from app.models import IngredientAlias, Recipe, RecipeStep, RecipeIngredient, SourceArchive
from app.services.identity import hash_password
from app.services.recipe_import import normalize_name, standard_dish_id
from app.services.meal_request import MealRequestDraft, dump

FOODS = {'FOOD001566': '雞蛋', 'FOOD001016': '番茄', 'FOOD000867': '高麗菜',
         'FOOD000039': '大蒜', 'FOOD000585': '青江菜', 'FOOD000678': '紅蘿蔔',
         'FOOD000004': '九層塔', 'DEMO-TOFU': '豆腐', 'DEMO-MUSHROOM': '菇類'}
RECIPES = [
    ('DM01', '番茄炒蛋', ['FOOD001016', 'FOOD001566'], '主菜', '炒'),
    ('DM02', '蒜炒高麗菜', ['FOOD000867', 'FOOD000039'], '副菜', '炒'),
    ('DM03', '青江菜豆腐湯', ['FOOD000585', 'DEMO-TOFU'], '湯', '煮'),
    ('DM04', '紅蘿蔔炒蛋', ['FOOD000678', 'FOOD001566'], '主菜', '炒'),
    ('DM05', '塔香豆腐', ['FOOD000004', 'DEMO-TOFU'], '主菜', '煎'),
    ('DM06', '鮮菇蔬菜湯', ['DEMO-MUSHROOM', 'FOOD000678'], '湯', '煮'),
]
with SessionLocal() as db, db.begin():
    if db.scalar(select(User).where(User.email == 'demo@example.test')):
        print('Existing isolated demonstration data retained')
    else:
        db.execute(text("UPDATE recipe_data_releases SET active = (release_id = 'legacy-1017')"))
        for fid, name in FOODS.items():
            db.add(IngredientAlias(food_id=fid, canonical_name=name, aliases=[], review_status='demo_only'))
        db.flush()
        for rid, name, foods, category, method in RECIPES:
            db.add(SourceArchive(raw_id='RAW-'+rid, source_name='介面展示合成資料', source_url='https://example.invalid/demo',
                source_recipe_id=rid, recipe_name_raw=name, servings_raw='2人份', ingredients_raw='合成展示配方',
                steps_raw='示範步驟', tags_raw='demo', content_hash='d'*64, import_batch_id='ui-demo',
                rights_status='demo_only', review_status='demo_only'))
            db.flush()
            db.add(Recipe(recipe_id=rid, raw_id='RAW-'+rid, standard_dish_id=standard_dish_id(name), recipe_name=name,
                normalized_name=normalize_name(name), name_aliases=[], servings_raw='2人份', original_servings=2,
                servings_status='parsed', category=category, meal_type='晚餐', cuisine='台式',
                source_name='介面展示合成資料', source_url='https://example.invalid/demo', cooking_methods=[method],
                is_eligible=True, review_status='demo_only', cooking_method_status='demo_only'))
            db.flush()
            for i, fid in enumerate(foods, 1):
                db.add(RecipeIngredient(recipe_ingredient_id=f'{rid}-I{i}', recipe_id=rid, sequence=i,
                    raw_text=f'{FOODS[fid]} 100g', canonical_name=FOODS[fid], food_id=fid,
                    amount=100, unit='g', quantity_status='parsed', unit_status='parsed', weight_g=100,
                    weight_status='parsed', weight_basis='raw'))
            for i, step in enumerate(['洗淨食材並切成適當大小。', f'將食材以{method}的方式煮熟，再依口味調味。'], 1):
                db.add(RecipeStep(step_id=f'{rid}-S{i}', recipe_id=rid, step_no=i, instruction_clean=step, cooking_method=method))
        user = User(email='demo@example.test', password_hash=hash_password('DemoLocal2026!'))
        home = Household(name='今天吃啥・示範家庭')
        db.add_all([user, home]); db.flush()
        db.add(HouseholdMembership(user_id=user.id, household_id=home.id, role='owner'))
        people = []
        for name, sex, weight, birth in [('爸爸（示範）', 'M', '70', date(1990, 5, 1)), ('媽媽（示範）', 'F', '55', date(1992, 8, 15))]:
            p = Member(household_id=home.id, display_name=name, birth_date=birth, equation_sex=sex,
                weight_kg=Decimal(weight), activity_category='sedentary', goal='maintain', portion=Decimal('1'),
                allergen_ingredient_ids=[], avoided_ingredient_ids=[])
            db.add(p); people.append(p)
        db.flush()
        today = date.today()
        draft = MealRequestDraft(meal_date=today, meal_type='dinner', budget='500', staple='不需要主食', dish_count=3,
            taste_preferences='清淡', specified_dishes=['番茄炒蛋', '蒜炒高麗菜', '青江菜豆腐湯'],
            participants=[{'member_id': str(p.id), 'dining': True, 'bento': False, 'bento_meal': 'lunch'} for p in people])
        payload = dump(draft)
        row = MealRequest(household_id=home.id, status='draft', version=1, meal_date=today, payload=payload)
        db.add(row); db.flush()
        db.add(MealRequestRevision(meal_request_id=row.id, version=1, payload=payload, created_by=user.id))
        print('Created synthetic account, family, 2 members, 6 recipes and one draft')
print('Isolated demonstration initialization complete')
