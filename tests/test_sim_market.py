from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import current_household_id
from app.api.errors import register_error_handlers
from app.api.sim_market import router
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models.recipe import IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient, SourceArchive


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
