"""Capacity pricing through generation, scoped quote reads, and price retry without services."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import AdoptedMealPlan, GenerationResult
from app.estimation.store import FactorSnapshot
from app.meal_calc.types import IngredientRequirement
from app.models.recipe import Allergen, IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient, RecipeStep, SourceArchive
from app.services import generation, meal_calculation, meal_plans, meal_results, result_retry
from app.services.meal_cost import compute_meal_cost, meal_cost_from_db
from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
from recipe_workbook import build, ingredient, recipe
from test_t12_meal_calculation import FakeCalculator, single
from test_t13_meal_cost import NOW, quote

D = Decimal
FOOD = "TEST-LIQUID"


def capacity(amount):
    return IngredientRequirement(FOOD, volume_ml=Fraction(amount))


def snapshot(product, *, snapshot_id=1, net_mass_g=None, historical=False):
    return {
        "id": snapshot_id, "food_id": FOOD, "quote_payload": product.model_dump(mode="json"),
        "recipe_release_id": "synthetic", "price_data_release_id": "historical" if historical else None,
        "usage_status": "historical_requires_refresh" if historical else None,
        "net_mass_g": net_mass_g, "mass_source_url": "https://example.test/label",
    }


@pytest.mark.parametrize(("amount", "budget", "packs", "status"), [
    ("100", "30", 1, "OK"), ("100", "29.99", 1, "BUDGET_EXCEEDED"),
    ("500", "150", 1, "OK"), ("500.00000000000000000000000001", "150", 2, "BUDGET_EXCEEDED"),
])
def test_volume_packages_and_budget(amount, budget, packs, status):
    result = compute_meal_cost([capacity(amount)], [], D(budget),
                               lambda _: quote(FOOD, "150", "0.5", "L"), now=NOW)
    [line] = result.lines
    assert line.shortage_grams is None
    assert line.shortage_ml == Fraction(amount)
    assert (line.packs, line.purchase_price) == (packs, D(150) * packs)
    assert line.exact_cost == Fraction(amount) * Fraction(3, 10)
    assert result.budget_status == status


@pytest.mark.parametrize(("unit", "extra", "reason"), [
    ("g", {}, "UNIT_MISMATCH"),
    ("ml", {"currency": "USD"}, "CURRENCY_MISMATCH"),
    ("ml", {"valid_from": NOW + timedelta(seconds=1)}, "PRICE_NOT_YET_VALID"),
    ("ml", {"valid_until": NOW - timedelta(seconds=1)}, "PRICE_EXPIRED"),
])
def test_capacity_with_unusable_quote_stays_unknown(unit, extra, reason):
    result = compute_meal_cost([capacity(100)], [], D(500),
                               lambda _: quote(FOOD, "150", "500", unit, **extra), now=NOW)
    [line] = result.lines
    assert line.missing_reason == reason
    assert (line.packs, line.cost, line.purchase_price) == (None, None, None)
    assert (result.cost_total, result.purchase_total, result.budget_status) == (None, None, "PRICE_INCOMPLETE")


def test_current_volume_selected_from_one_batch_and_survives_verified_mass_projection():
    db = Mock()
    db.execute.return_value.mappings.return_value = [
        snapshot(quote(FOOD, "150", "500", "ml"), net_mass_g=D(620)),
        snapshot(quote(FOOD, "1", "500", "g", retailer="萬家福"), snapshot_id=2),
    ]
    result = meal_cost_from_db(db, [capacity(100)], [], D(30), now=NOW)
    db.execute.assert_called_once()
    [line] = result.lines
    assert (line.cost, line.purchase_price, line.quote.package_unit, line.quote.snapshot_id) == (30, 150, "ml", 1)
    db.execute.return_value.mappings.return_value = [snapshot(quote(FOOD, "150", "500", "ml"), historical=True)]
    result = meal_cost_from_db(db, [capacity(100)], [], D(30), now=NOW)
    assert result.lines[0].missing_reason == "PRICE_REVALIDATION_REQUIRED"
    assert result.cost_total is None


def test_known_capacity_cost_over_budget_wins_over_unquantified_food():
    db = Mock()
    db.execute.return_value.mappings.return_value = [snapshot(quote(FOOD, "150", "500", "ml"))]
    cost = meal_cost_from_db(db, [capacity(100)], [], D("29.99"), now=NOW,
                             unquantified_food_ids=["OTHER-FOOD"])
    assert (cost.known_cost_total, cost.cost_total, cost.budget_status) == (D(30), None, "BUDGET_EXCEEDED")


def test_future_capacity_quote_cannot_be_used_via_its_net_mass():
    db = Mock()
    db.execute.return_value.mappings.return_value = [snapshot(
        quote(FOOD, "150", "500", "ml", valid_from=NOW + timedelta(days=1)), net_mass_g=D(620),
    )]
    item = IngredientRequirement(FOOD, D(80), Fraction(100))
    result = meal_cost_from_db(db, [item], [], D(30), now=NOW)
    assert result.lines[0].missing_reason == "PRICE_NOT_YET_VALID"
    assert result.purchase_total is None


def test_requirements_merge_same_unit_without_treating_grams_as_volume():
    liquid = meal_calculation.IngredientLine(FOOD, "液體", D(10), pricing_volume_ml_per_n=Fraction(100))
    required = meal_calculation.pricing_requirements([liquid, liquid], [D(10), D(5)], D(3))
    assert required == [IngredientRequirement(FOOD, D(45), Fraction(450))]
    [line] = compute_meal_cost(required, [], D(135), lambda _: quote(FOOD, "150", "500", "ml"), now=NOW).lines
    assert (line.packs, line.cost) == (1, D(135))

    grams_only = meal_calculation.IngredientLine(FOOD, "液體", D(10))
    volume_only = meal_calculation.IngredientLine(FOOD, "液體", None, pricing_volume_ml_per_n=Fraction(100))
    assert meal_calculation.pricing_requirements([grams_only, volume_only], [D(10), None], D(1)) == [
        IngredientRequirement(FOOD, D(10)), capacity(100),
    ]
    estimated_volume = meal_calculation.IngredientLine(FOOD, "液體", None, volume_ml_per_n=D(100),
                                                      volume_is_estimated=True)
    assert meal_calculation.pricing_requirements([estimated_volume], [None], D(1)) == [
        IngredientRequirement(FOOD, None),
    ]
    estimated_weight = meal_calculation.IngredientLine(FOOD, "液體", D(100), pricing_volume_ml_per_n=Fraction(100),
                                                      weight_source="unit_estimate")
    assert meal_calculation.pricing_requirements([estimated_weight], [D(100)], D(1)) == [capacity(100)]


def test_workbook_liters_price_at_exact_serving_scale_in_sqlite(tmp_path):
    food_id = "FOOD999999"
    liquid = ingredient(food_id, "測試液體", None, raw_text="測試液體 0.5 L", amount=D("0.5"), unit="L")
    book = build(tmp_path / "recipes.xlsx", [recipe("R001", "合成食譜", servings="3人份", ingredients=[liquid])])
    bundle = parse_recipe_workbook(book)
    tables = [RecipeDataRelease, SourceArchive, Recipe, RecipeStep, RecipeIngredient, IngredientAlias, Allergen]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Recipe.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    with Session(engine) as db:
        import_recipe_bundle(db, bundle)
        [line] = meal_calculation.load_recipe_lines(db, "R001", FactorSnapshot())
        assert line.grams_per_n is None
        assert line.pricing_volume_ml_per_n == Fraction(500, 3)
        targets = meal_calculation.compose_targets([
            meal_calculation.ParticipationBaseline(str(i), D(600), D(30), D(1)) for i in range(3)
        ])
        calc = meal_calculation.calculate_meal([line], targets, mode="database", calculator=FakeCalculator())
        assert calc["requirements"] == [IngredientRequirement(food_id, volume_ml=Fraction(500))]
        result = compute_meal_cost(calc["requirements"], [], D(150),
                                   lambda _: quote(food_id, "150", "500", "mL"), now=NOW)
        assert (result.lines[0].packs, result.cost_total, result.budget_status) == (1, D(150), "OK")
        assert calc["final"]["energy_kcal"] is None


def test_capacity_only_keeps_nutrition_unknown_and_price_retry_keeps_exact_quantity(monkeypatch):
    # The display quantity is rounded by Decimal division. The price uses the exact source amount / servings.
    liquid = meal_calculation.IngredientLine(FOOD, "液體", None, volume_ml_per_n=D(100) / 3,
                                            pricing_volume_ml_per_n=Fraction(100, 3))
    calc = meal_calculation.calculate_meal([liquid], single("1"), mode="database", calculator=FakeCalculator())
    assert calc["ingredients"][0]["whole_meal_grams"] is None
    assert calc["final"]["energy_kcal"] is None
    assert not calc["adoptable"]

    db = Mock()
    db.execute.return_value.mappings.return_value = []
    ctx = SimpleNamespace(factory=lambda: nullcontext(db), input_payload=lambda: {}, stage=Mock(),
                          job=SimpleNamespace(id="job", params={"source_result_version": 1}))
    draft = SimpleNamespace(available_ingredient_ids=[], budget=D(10))
    payload = generation._jsonable({"calculation": calc, "cost": generation._cost_view(ctx, draft, calc, NOW)})
    assert payload["calculation"]["requirements"] == [{"food_id": FOOD, "grams": None, "volume_ml": "100/3"}]
    assert payload["cost"]["budget_status"] == "PRICE_INCOMPLETE"
    assert payload["cost"]["unquantified_food_ids"] == []

    db.execute.return_value.mappings.return_value = [snapshot(quote(FOOD, "150", "500", "ml"))]
    monkeypatch.setattr(result_retry, "_source", lambda _: {**payload})
    monkeypatch.setattr(result_retry.meal_request_service, "load", lambda _: draft)
    price_lookup = Mock(return_value={})
    fresh = result_retry.make_price_retry_handler(price_lookup=price_lookup, now=NOW)(ctx)
    price_lookup.assert_called_once_with([FOOD])
    projected = meal_results._prices(fresh)
    assert (projected["budget_status"], projected["cost_total"], projected["purchase_total"]) == ("OK", "10", "150")
    [item] = projected["items"]
    assert item["shortage_grams"] is None
    assert item["shortage_ml"] == "100/3"
    assert fresh["calculation"] == payload["calculation"]
    assert payload["cost"]["budget_status"] == "PRICE_INCOMPLETE"


def test_legacy_gram_result_can_retry_and_adopted_snapshot_keeps_original_prices(monkeypatch):
    legacy = {
        "dishes": [],
        "calculation": {"requirements": [{"food_id": FOOD, "grams": "100"}]},
        "cost": {"status": "computed", "budget_status": "PRICE_INCOMPLETE", "price_complete": False,
                 "purchase_total": None, "cost_total": None, "known_purchase_total": "0", "known_cost_total": "0",
                 "lines": [{"food_id": FOOD, "shortage_grams": "100", "price_status": "unknown"}]},
    }
    original = deepcopy(legacy)
    source = GenerationResult(id=uuid4(), payload=legacy)
    plan = AdoptedMealPlan(
        id=uuid4(), household_id=uuid4(), meal_date=NOW.date(), meal_type="dinner", mode="database_menu",
        status="adopted", adopted_at=NOW, dishes=[], price_exception_accepted=True,
        snapshot={"result": deepcopy(legacy), "request": {},
                  "previous_day": {"at_adoption": {}, "history_changed": False}},
    )
    db = Mock()
    db.get.return_value = source
    db.execute.return_value.mappings.return_value = [snapshot(quote(FOOD, "60", "300", "g"))]
    ctx = SimpleNamespace(factory=lambda: nullcontext(db), input_payload=lambda: {}, stage=Mock(),
                          job=SimpleNamespace(id="job", params={"source_result_version": 1,
                                                               "source_result_id": str(source.id)}))
    draft = SimpleNamespace(available_ingredient_ids=[], budget=D(20))
    monkeypatch.setattr(result_retry.meal_request_service, "load", lambda _: draft)
    retried = result_retry.make_price_retry_handler(price_lookup=Mock(return_value={}), now=NOW)(ctx)
    assert (retried["cost"]["cost_total"], retried["cost"]["purchase_total"], retried["cost"]["budget_status"]) == (
        "20", "60", "OK",
    )
    assert retried["calculation"] == original["calculation"]
    assert source.payload == original
    historical = meal_plans.detail(plan)
    assert historical["prices"]["cost_total"] is None
    [item] = historical["prices"]["items"]
    assert item["shortage_grams"] == "100"
    assert "shortage_ml" not in item
    assert plan.snapshot["result"] == original
