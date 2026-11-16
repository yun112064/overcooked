"""模擬超市目錄與一鍵購入的設定／快照檢查：合成 SQLite，不連資料庫、不連真實商店。"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

# 設定物件要求 DATABASE_URL；這些測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.api import sim_market
from app.api.deps import current_auth, current_household_id
from app.api.errors import register_error_handlers
from app.core.config import settings
from app.db.models import AdoptedMealPlan, Household, HouseholdMembership, User, UserSession
from app.db.session import get_db
from app.models.recipe import IngredientAlias, Recipe, RecipeIngredient, SourceArchive
from app.services import market_handoff

ORIGIN = 'http://localhost:5174'
APP_ORIGIN = 'http://localhost:8080'
KEY = 'test-only-key-not-for-deployment-123456789'
FOODS = [('F-MILK', '牛奶'), ('F-PORK', '豬里肌'), ('F-RICE', '白米'), ('F-SALT', '鹽'), ('F-UNUSED', '鮭魚')]
SHORTAGES = {'recipe_release_id': None,
             'items': [{'food_id': 'F-PORK', 'name': '豬里肌', 'shortage_grams': '150'}],
             'unquantified_items': [{'food_id': 'F-SALT', 'name': '鹽'}]}


def _seed_recipes(db):
    db.add(SourceArchive(raw_id='RAW-R1', source_name='合成來源', source_url='https://example.invalid/R1',
                         source_recipe_id='R1', recipe_name_raw='合成料理', servings_raw='2人份',
                         ingredients_raw='', steps_raw='', tags_raw='', content_hash='x' * 64,
                         import_batch_id='sim-market'))
    db.add(Recipe(recipe_id='R1', raw_id='RAW-R1', standard_dish_id='D1', recipe_name='合成料理',
                  normalized_name='合成料理', servings_raw='2人份', original_servings=2, servings_status='parsed',
                  category='主菜', meal_type='晚餐', cuisine='台式', source_name='合成來源',
                  source_url='https://example.invalid/R1', is_eligible=True, review_status='approved'))
    for food_id, name in FOODS:
        db.add(IngredientAlias(food_id=food_id, canonical_name=name))
    db.flush()
    for seq, (food_id, name) in enumerate(FOODS[:4], start=1):
        # 牛奶以毫升記載：DEMO 的食材表沒有容量證據欄位，仍應以克數包裝販售
        unit = 'ml' if food_id == 'F-MILK' else 'g'
        db.add(RecipeIngredient(recipe_ingredient_id=f'R1-I{seq}', recipe_id='R1', sequence=seq, raw_text=name,
                                canonical_name=name, food_id=food_id, amount=100, unit=unit,
                                quantity_status='source_exact', unit_status='parsed', weight_g=100,
                                weight_status='parsed'))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, 'sim_market_url', ORIGIN)
    monkeypatch.setattr(settings, 'sim_market_handoff_key', KEY)
    monkeypatch.setattr(settings, 'allowed_origins', APP_ORIGIN)
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', SQLiteTypeCompiler.visit_JSON, raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    user, household, plan_id = uuid4(), uuid4(), uuid4()
    session_hash = 'b' * 64
    with Session(engine) as db, db.begin():
        for model in (User, Household, HouseholdMembership, UserSession, AdoptedMealPlan,
                      SourceArchive, Recipe, IngredientAlias, RecipeIngredient):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace('::jsonb', '')
            db.connection().exec_driver_sql(ddl)
        db.add(User(id=user, email='synthetic@example.invalid', password_hash='fixture'))
        db.add(Household(id=household, name='Synthetic household'))
        db.add(HouseholdMembership(user_id=user, household_id=household))
        db.add(UserSession(token_hash=session_hash, user_id=user, household_id=household,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.add(AdoptedMealPlan(id=plan_id, household_id=household, meal_date=date(2026, 11, 10),
                              meal_type='dinner', mode='database_menu', status='adopted', version=1,
                              dishes=[{'dish_name': '合成料理'}], snapshot={'market_shortages': SHORTAGES}))
        _seed_recipes(db)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(sim_market.router)

    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
        user_id=user, session=SimpleNamespace(token_hash=session_hash))
    app.dependency_overrides[current_household_id] = lambda: household
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, plan_id=plan_id)
    engine.dispose()


def issue(env):
    return env.client.post('/sim-market/handoffs', json={'plan_id': str(env.plan_id)},
                           headers={'origin': APP_ORIGIN})


def code(response):
    return response.json()['error']['code']


def update_plan(env, **values):
    with Session(env.engine) as db, db.begin():
        plan = db.get(AdoptedMealPlan, env.plan_id)
        for key, value in values.items():
            setattr(plan, key, value)


def test_catalog_lists_recipe_foods_with_gram_packs_and_constant_id(env):
    response = env.client.get('/sim-market/catalog')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['recipe_release_id'] == market_handoff.CATALOG_ID == 'recipe_dataset_1027'
    assert body['real_checkout'] is False
    products = {row['food_id']: row for row in body['products']}
    assert set(products) == {'F-MILK', 'F-PORK', 'F-RICE', 'F-SALT'}  # 未被食譜使用的食材不上架
    assert all(row['package_grams'] and 'package_ml' not in row and row['price_cents'] > 0
               for row in products.values())
    assert products['F-PORK']['category'] == '肉品海鮮'
    assert products['F-RICE'] == {'food_id': 'F-RICE', 'name': '白米', 'category': '米麵雜糧',
                                  'package_grams': 600, 'price_cents': 6900}


def test_checkout_release_matches_catalog_and_keeps_unknown_items(env):
    token = issue(env).json()['checkout_url'].split('#handoff=')[1]
    response = env.client.post('/sim-market/checkout', json={'token': token}, headers={'origin': ORIGIN})
    assert response.status_code == 200, response.text
    body = response.json()
    shortages = body['plan']['snapshot']['market_shortages']
    assert shortages['recipe_release_id'] == body['catalog']['recipe_release_id'] == market_handoff.CATALOG_ID
    assert shortages['unquantified_items'] == [{'food_id': 'F-SALT', 'name': '鹽'}]
    assert 'order' not in body


def test_orders_route_is_absent(env):
    assert env.client.post('/sim-market/orders', json={'token': 'x'}, headers={'origin': ORIGIN}).status_code == 404


@pytest.mark.parametrize('url', [None, '', 'http://market.example', 'http://localhost:5174/market'])
def test_handoff_unavailable_without_usable_market_url(env, monkeypatch, url):
    monkeypatch.setattr(settings, 'sim_market_url', url)
    response = issue(env)
    assert response.status_code == 503 and code(response) == 'MARKET_UNAVAILABLE'


@pytest.mark.parametrize('key', [None, '', 'k' * 31])
def test_handoff_unavailable_with_short_key(env, monkeypatch, key):
    monkeypatch.setattr(settings, 'sim_market_handoff_key', key)
    response = issue(env)
    assert response.status_code == 503 and code(response) == 'MARKET_UNAVAILABLE'


def test_superseded_plan_cannot_be_handed_off(env):
    update_plan(env, status='superseded')
    response = issue(env)
    assert response.status_code == 409 and code(response) == 'MARKET_PLAN_UNAVAILABLE'


@pytest.mark.parametrize('snapshot', [{}, {'nutrition': {'totals': {}}}, {'result': {}}])
def test_snapshot_without_shortages_or_result_is_missing(env, snapshot):
    update_plan(env, snapshot=snapshot)
    response = issue(env)
    assert response.status_code == 409 and code(response) == 'MARKET_SNAPSHOT_MISSING'
