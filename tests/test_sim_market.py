from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.api import meal_plans, meal_requests
from app.api.deps import current_auth, current_household_id
from app.api.errors import register_error_handlers
from app.api.sim_market import router
from app.core.config import settings
from app.db.base import Base
from app.db.models import AdoptedMealPlan, Household, MealRequest, Member
from app.db.session import get_db
from app.models.recipe import IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient, SourceArchive
from app.services import database_menu_plans


@pytest.fixture
def market_client(monkeypatch):
    monkeypatch.setattr(settings, "generation_recipe_release_id", None)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        model.__table__ for model in (RecipeDataRelease, SourceArchive, Recipe, IngredientAlias, RecipeIngredient)
    ])
    with Session(engine) as db, db.begin():
        for release_id, active, food_id, name in (
            ("test-current", True, "TEST-CABBAGE", "高麗菜"),
            ("test-older", False, "TEST-FISH", "鯛魚"),
        ):
            db.add(RecipeDataRelease(release_id=release_id, dataset_version=release_id, source_sha256="0" * 64,
                                     schema_version="test", source_kind="xlsx_import", active=active))
            db.add(IngredientAlias(food_id=food_id, recipe_release_id=release_id, canonical_name=name))
            for seq in range(2):
                recipe_id = f"{release_id}-{seq}"
                db.add(SourceArchive(raw_id=recipe_id, recipe_release_id=release_id, source_name="synthetic",
                                     source_url="https://example.invalid/", source_recipe_id=recipe_id,
                                     recipe_name_raw=recipe_id, servings_raw="2", ingredients_raw=name,
                                     steps_raw="synthetic", tags_raw="", content_hash="0" * 64,
                                     import_batch_id=release_id))
                db.add(Recipe(recipe_id=recipe_id, recipe_release_id=release_id, raw_id=recipe_id,
                              standard_dish_id=recipe_id, recipe_name=recipe_id, normalized_name=recipe_id,
                              servings_raw="2", servings_status="known", category="主菜", meal_type="晚餐",
                              cuisine="台式", source_name="synthetic", source_url="https://example.invalid/"))
                db.add(RecipeIngredient(recipe_ingredient_id=recipe_id, recipe_id=recipe_id,
                                        recipe_release_id=release_id, sequence=1, raw_text=name,
                                        canonical_name=name, food_id=food_id, quantity_status="known",
                                        unit_status="known", weight_status="known"))
        db.add(IngredientAlias(food_id="TEST-UNUSED", recipe_release_id="test-current", canonical_name="未使用"))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield app, client
    engine.dispose()


def test_catalog_rejects_unauthenticated_requests(market_client):
    _, client = market_client
    assert client.get("/sim-market/catalog").status_code == 401


def test_catalog_lists_only_used_food_ids_from_the_active_release(market_client):
    app, client = market_client
    app.dependency_overrides[current_household_id] = lambda: "test-household"
    response = client.get("/sim-market/catalog")
    assert response.status_code == 200
    assert response.json() == {
        "recipe_release_id": "test-current",
        "products": [{"food_id": "TEST-CABBAGE", "name": "高麗菜", "category": "蔬果菇類",
                      "package_grams": 500, "price_cents": 5900}],
    }


def test_explicit_release_is_not_replaced_by_the_active_release(market_client, monkeypatch):
    app, client = market_client
    app.dependency_overrides[current_household_id] = lambda: "test-household"
    monkeypatch.setattr(settings, "generation_recipe_release_id", "test-older")
    result = client.get("/sim-market/catalog").json()
    assert result["recipe_release_id"] == "test-older"
    assert [item["food_id"] for item in result["products"]] == ["TEST-FISH"]

    monkeypatch.setattr(settings, "generation_recipe_release_id", "test-unavailable")
    assert client.get("/sim-market/catalog").json() == {"recipe_release_id": None, "products": []}


def test_picker_adoption_freezes_exact_market_shortages(market_client, monkeypatch):
    app, client = market_client
    app.include_router(meal_plans.router)
    app.include_router(meal_requests.router)
    household_id, member_id, request_id = uuid4(), uuid4(), uuid4()
    user_id = uuid4()
    app.dependency_overrides[current_household_id] = lambda: household_id
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=user_id)
    monkeypatch.setattr(meal_plans.service, '_lock_household', lambda *_: None)

    # Only the tables exercised by these endpoints, using SQLite JSON in place of JSONB.
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', SQLiteTypeCompiler.visit_JSON, raising=False)
    db_session = contextmanager(app.dependency_overrides[get_db])
    with db_session() as db, db.begin():
        for model in (Household, Member, MealRequest, AdoptedMealPlan):
            ddl = CreateTable(model.__table__).compile(dialect=db.bind.dialect)
            db.connection().exec_driver_sql(str(ddl).replace('::jsonb', ''))
        db.add(Household(id=household_id, name='Synthetic household'))
        db.add(Member(id=member_id, household_id=household_id, display_name='Test member',
                      birth_date=date(1990, 1, 1), equation_sex='F', weight_kg=60,
                      activity_category='active', goal='general', portion=1))
        db.add(MealRequest(id=request_id, household_id=household_id, payload={
            'mode': 'picker', 'meal_date': '2026-11-09', 'meal_type': 'dinner', 'dish_count': 2,
            'staple': '不需要主食', 'specified_dishes': ['A', 'B'],
            'participants': [{'member_id': str(member_id), 'dining': True}],
            'available_ingredient_ids': ['TEST-OWNED'],
        }))

    recipe_ids = ['test-current-0', 'test-current-1']
    selected = [{'recipe_id': rid, 'recipe_name': rid, 'standard_dish_id': rid} for rid in recipe_ids]
    monkeypatch.setattr(database_menu_plans, 'current_menu', lambda *_: {'selected': selected})
    preview = {'status': 'nutrition_passed', 'dishes': [
        {'ingredients': [{'food_id': 'TEST-CABBAGE', 'name': '高麗菜', 'grams': '500'},
                         {'food_id': 'TEST-OWNED', 'name': '已有食材', 'grams': '100'},
                         {'food_id': 'TEST-UNKNOWN', 'name': '用量未知', 'grams': '20'}]},
        {'ingredients': [{'food_id': 'TEST-CABBAGE', 'name': '高麗菜', 'grams': '1/3'},
                         {'food_id': 'TEST-UNKNOWN', 'name': '用量未知', 'grams': None}]},
    ], 'staple': {'food_id': 'TEST-RICE', 'name': '白飯', 'final_grams': '160'}}
    monkeypatch.setattr(database_menu_plans.t12_planning, 'preview', lambda *_, **__: preview)
    body = {'meal_request_id': str(request_id), 'request_version': 1, 'recipe_ids': recipe_ids}
    headers = {'Origin': 'http://localhost:8080', 'Idempotency-Key': 'test-picker-adopt'}
    response = client.post('/meal-plans', json=body, headers=headers)
    assert response.status_code == 201, response.text
    summary = response.json()['meal_plan']
    assert summary['market_shortages_available']
    path = f"/meal-plans/{summary['id']}"
    detail = client.get(path).json()
    assert detail['result_version'] is None
    assert detail['snapshot']['market_shortages'] == {
        'recipe_release_id': 'test-current',
        'items': [{'food_id': 'TEST-CABBAGE', 'name': '高麗菜', 'shortage_grams': '1501/3'},
                  {'food_id': 'TEST-RICE', 'name': '白飯', 'shortage_grams': '160'}],
        'unquantified_items': [{'food_id': 'TEST-UNKNOWN', 'name': '用量未知'}],
    }

    # A later selection or catalogue change cannot rewrite a saved plan or its cart inputs.
    monkeypatch.setattr(database_menu_plans, 'current_menu', lambda *_: pytest.fail('adopted menu was recomputed'))
    monkeypatch.setattr(settings, 'generation_recipe_release_id', 'test-older')
    reopened = client.get(f'/meal-requests/{request_id}/recipes').json()
    assert reopened['selected'] == selected
    assert reopened['adopted_plan'] == summary
    retry = client.post('/meal-plans', json=body, headers=headers)
    assert retry.status_code == 200
    assert retry.json()['meal_plan'] == summary
    assert client.get(path).json() == detail
    app.dependency_overrides[current_household_id] = uuid4
    assert client.get(path).status_code == 404
