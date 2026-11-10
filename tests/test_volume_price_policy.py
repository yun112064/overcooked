"""Capacity prices preserve manual policy, exact package boundaries and unknown demand."""
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import settings
from app.meal_calc.types import IngredientRequirement
from app.management.models import tables as T
from app.management.price_policy import ManagedPrice, PricePreview, preview_price
from app.services import budget_floor, meal_cost
from app.services.generation import cost_snapshot
from app.source_adapters.price_scope import quote_unusable_reason
from test_management_price import (ACTORS, AT, FOOD, RELEASE, binding, correction, price_env,
                                   quote as managed_fixture_quote, save_fixture_observation)
from test_t13_meal_cost import NOW, quote

D = Decimal


def reviewed_volume(*, as_of=AT, allow_volume=True):
    entry = ManagedPrice("reviewed-volume", "volume-review", correction(
        package_mass_g=None, package_volume_ml="500", price_twd="150"))
    return preview_price([entry], [], None, food_id=FOOD, release_id=RELEASE,
                         dictionary_id="dictionary-v1", ingredient_version_id="dictionary-food",
                         as_of=as_of, allow_volume=allow_volume)


def test_reviewed_volume_has_explicit_scope_and_no_gram_fallback():
    decision = reviewed_volume()
    assert (decision.quote.package_unit, decision.quote.package_quantity, decision.quote.price) == ("ml", D(500), D(150))
    assert decision.quote.manual_revision_id == "reviewed-volume"
    assert quote_unusable_reason(decision.quote, AT) == "UNIT_MISMATCH"
    assert quote_unusable_reason(decision.quote, AT, allow_volume=True) is None
    assert reviewed_volume(allow_volume=False).reason == "UNIT_MISMATCH"
    assert reviewed_volume(as_of=AT + timedelta(days=3)).reason == "PRICE_EXPIRED"


def test_mixed_units_keep_unpriced_part_and_do_not_infer_density():
    demand = [IngredientRequirement(FOOD, D(50)), IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    result = meal_cost.compute_meal_cost(demand, [], D(100), lambda _: quote(FOOD, "150", "500", "ml"), now=NOW)
    assert result.budget_status == "PRICE_INCOMPLETE"
    assert result.purchase_total is None
    assert result.known_cost_total == D(30)
    assert result.lines[0].missing_reason == "UNIT_MISMATCH"
    assert result.lines[0].shortage_grams == D(50)
    assert result.lines[1].shortage_ml == Fraction(100)


def test_same_product_rounds_after_compatible_demands_are_added():
    demand = [IngredientRequirement(FOOD, D(240), Fraction(200)),
              IngredientRequirement(FOOD, volume_ml=Fraction(200))]
    result = meal_cost.compute_meal_cost(demand, [], D(120), lambda _: quote(FOOD, "150", "500", "ml"), now=NOW)
    assert len(result.lines) == 1
    line = result.lines[0]
    assert (line.shortage_ml, line.packs, line.purchase_price, line.cost) == (Fraction(400), 1, D(150), D(120))
    assert line.required_grams is None


def test_unquantified_component_does_not_disappear_beside_known_capacity():
    demand = [IngredientRequirement(FOOD), IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    result = meal_cost.compute_meal_cost(demand, [], D(500), lambda _: quote(FOOD, "150", "500", "ml"), now=NOW)
    assert not result.price_complete and result.purchase_total is None
    assert len(result.lines) == 2 and result.known_cost_total == D(30)


def test_volume_snapshot_keeps_exact_fraction_for_shopping():
    demand = [IngredientRequirement(FOOD, volume_ml=Fraction(100, 3))]
    result = meal_cost.compute_meal_cost(demand, [], D(10), lambda _: quote(FOOD, "150", "500", "ml"), now=NOW)
    assert cost_snapshot(result)["lines"][0]["shortage_ml"] == "100/3"


@pytest.mark.parametrize("unit, package, expected_cost", [("g", "600", "30"), ("ml", "500", "30")])
def test_dual_source_quantity_survives_either_price_dimension(unit, package, expected_cost):
    demand = IngredientRequirement(FOOD, D(120), Fraction(100))
    result = meal_cost.compute_meal_cost([demand], [], D(100),
                                         lambda _: quote(FOOD, "150", package, unit), now=NOW)
    line = result.lines[0]
    assert line.cost == D(expected_cost)
    assert line.shortage_grams == D(120) and line.shortage_ml == Fraction(100)
    frozen = cost_snapshot(result)["lines"][0]
    assert frozen["shortage_grams"] == D(120) and frozen["shortage_ml"] == "100"


def test_unknown_quote_keeps_both_original_shortage_units():
    demand = IngredientRequirement(FOOD, D(120), Fraction(100))
    line = meal_cost.price_requirement(demand, None, NOW)
    assert line.cost is None and line.missing_reason == "NO_PRICE_DATA"
    assert line.shortage_grams == D(120) and line.shortage_ml == Fraction(100)


def test_mass_quote_rounding_combines_dual_and_mass_only_demands():
    demand = [IngredientRequirement(FOOD, D(120), Fraction(100)), IngredientRequirement(FOOD, D(120))]
    result = meal_cost.compute_meal_cost(demand, [], D(100),
                                         lambda _: quote(FOOD, "150", "600", "g"), now=NOW)
    assert len(result.lines) == 1
    assert (result.lines[0].shortage_grams, result.lines[0].packs, result.purchase_total) == (D(240), 1, D(150))
    # Only one component has a source volume, so the combined volume is unknown.
    assert result.lines[0].shortage_ml is None


def test_exact_sku_mass_and_volume_keep_source_shortages_without_duplicate_packs(monkeypatch):
    monkeypatch.setattr(settings, "management_price_policy_enabled", False)
    volume = quote(FOOD, "150", "500", "ml", snapshot_id=17, recipe_release_id=RELEASE)
    mass = volume.model_copy(update={"package_quantity": D(600), "package_unit": "g",
                                     "weight_basis": "manufacturer_net_mass"})
    monkeypatch.setattr(meal_cost, "cost_quote_candidates", lambda *args: {FOOD: [mass, volume]})
    demand = [IngredientRequirement(FOOD, D(120)), IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    result = meal_cost.meal_cost_from_db(None, demand, [], D(100), now=NOW)
    assert result.cost_total == D(60)
    assert result.purchase_total is None
    assert result.lines[0].shortage_grams == D(120) and result.lines[0].shortage_ml is None
    assert result.lines[1].shortage_ml == Fraction(100) and result.lines[1].shortage_grams is None
    assert all(line.packs is None and line.missing_reason == "PACKAGE_DEMAND_UNITS_REQUIRE_RECONCILIATION"
               for line in result.lines)


def test_manual_policy_replaces_raw_capacity_and_preserves_rejection(monkeypatch):
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    demand = [IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    monkeypatch.setattr(meal_cost, "cost_quote_candidates", lambda *args: {FOOD: [quote(FOOD, "1", "500", "ml")]})
    override = Mock(return_value={FOOD: reviewed_volume()})
    monkeypatch.setattr(meal_cost, "active_overrides", override)
    result = meal_cost.meal_cost_from_db(None, demand, [], D(30), now=AT, recipe_release_id=RELEASE)
    assert result.cost_total == D(30) and result.purchase_total == D(150)
    assert override.call_args.kwargs["allow_volume"] is True
    assert override.call_args.kwargs["recipe_release_id"] == RELEASE
    override.return_value = {FOOD: PricePreview(None, "PRICE_EXPIRED")}
    result = meal_cost.meal_cost_from_db(None, demand, [], D(30), now=AT, recipe_release_id=RELEASE)
    assert result.lines[0].missing_reason == "PRICE_EXPIRED" and result.purchase_total is None


def test_managed_sku_selection_uses_each_requirement_dimension(monkeypatch):
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    volume = quote(FOOD, "150", "500", "ml", snapshot_id=17)
    mass = volume.model_copy(update={"package_quantity": D(600), "package_unit": "g",
                                     "weight_basis": "manufacturer_net_mass"})
    monkeypatch.setattr(meal_cost, "cost_quote_candidates", lambda *args: {FOOD: [mass, volume]})
    override = Mock(side_effect=lambda *args, **kwargs: {
        FOOD: PricePreview(volume if kwargs["allow_volume"] else mass, None)})
    monkeypatch.setattr(meal_cost, "active_overrides", override)
    demand = [IngredientRequirement(FOOD, D(120)), IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    result = meal_cost.meal_cost_from_db(None, demand, [], D(100), now=NOW)
    assert {call.kwargs["allow_volume"] for call in override.call_args_list} == {False, True}
    assert [line.cost for line in result.lines] == [D(30), D(30)]
    assert [line.quote.package_unit for line in result.lines] == ["g", "ml"]
    assert result.lines[0].shortage_grams == D(120) and result.lines[1].shortage_ml == Fraction(100)


def test_budget_floor_uses_live_manual_binding_without_caching_it(monkeypatch):
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    demand = [IngredientRequirement(FOOD, volume_ml=Fraction(100))]
    floors = [budget_floor.RecipeFloor(str(i), str(i), "主菜", demand) for i in range(budget_floor.MIN_PRICED_DISHES)]
    monkeypatch.setattr(budget_floor, "_recipe_floors", lambda _: floors)
    monkeypatch.setattr(budget_floor, "_cached", lambda key, load, version=None: load())
    monkeypatch.setattr(budget_floor, "cost_quote_candidates", lambda _: {FOOD: [quote(FOOD, "1", "500", "ml")]})
    override = Mock(return_value={FOOD: reviewed_volume()})
    monkeypatch.setattr(budget_floor, "active_overrides", override)
    arguments = dict(dish_count=2, total_n=D(1), available_food_ids=[], has_staple=True, now=AT)
    assert budget_floor.minimum_budget(None, **arguments) == D(60)
    override.return_value = {FOOD: PricePreview(None, "PRICE_EXPIRED")}
    assert budget_floor.minimum_budget(None, **arguments) is None


def test_pg_volume_policy_activation_replacement_and_immutable_history(price_env, monkeypatch):
    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    save_fixture_observation(env, "cheap-capacity", managed_fixture_quote(
        package_unit="ml", package_quantity=D(500), price=D(1), product_id="cheap-volume"))

    def publish(price, *, previous=None, replacement=None):
        dependency = pins if previous is None else {
            **pins, "base_publication_id": previous["publication_id"],
            "base_publication_manifest_sha256": previous["manifest_hash"]}
        content = correction(**identity, package_mass_g=None, package_volume_ml="500", price_twd=price)
        if replacement:
            content["replaces_version_id"] = replacement
        proposal = env.content("price", content, dependency)
        return env.call(ACTORS[2], "publish", env.key(), {
            **env.approved(proposal), "base_publication_id": previous["publication_id"] if previous else None})

    def activate(publication, revision):
        body = {"publication_id": publication["publication_id"], "expected_binding_revision": revision, "reason": "synthetic capacity"}
        plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", body)
        assert plan["can_activate"]
        env.call(ACTORS[2], "activate", env.key(), "price_policy", {
            **body, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]})

    def meal(moment=AT):
        with Session(env.engine) as db:
            return meal_cost.meal_cost_from_db(db, [IngredientRequirement(FOOD, volume_ml=Fraction(100))],
                                               [], D(100), now=moment, recipe_release_id=RELEASE)

    original = publish("150")
    activate(original, 1)
    frozen = cost_snapshot(meal())
    assert frozen["cost_total"] == D(30) and frozen["purchase_total"] == D(150)
    members = T["publication_members"]
    old_version = env.engine.scalar(sa.select(members.c.version_id).where(members.c.publication_id == original["publication_id"]))
    successor = publish("200", previous=original, replacement=old_version)
    assert meal().cost_total == D(30)
    activate(successor, 2)
    assert meal().cost_total == D(40)
    assert frozen["cost_total"] == D(30)
    assert frozen["lines"][0]["quote"].price_policy_publication_id == original["publication_id"]
    assert meal(AT + timedelta(days=3)).lines[0].missing_reason == "PRICE_EXPIRED"


def test_pg_locked_volume_sku_preserves_mass_evidence_for_mass_only_demand(price_env, monkeypatch):
    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    product = managed_fixture_quote(package_unit="ml", package_quantity=D(500), price=D(150))
    save_fixture_observation(env, "locked-capacity-with-mass", product)
    snapshot_id = env.engine.scalar(sa.text("SELECT max(id) FROM price_quote_snapshots"))
    env.engine.execute(sa.text("""
        INSERT INTO price_package_mass_evidence
            (provider,product_id,product_name,volume_ml,net_mass_g,source_snapshot_id,source_url,source_detail)
        VALUES ('pxmart',:sku,:name,500,600,:snapshot,'https://example.invalid/label','Synthetic exact package mass')
    """), {"sku": product.product_id, "name": product.product_name, "snapshot": snapshot_id})
    proposal = env.content("price", binding(**identity), pins)
    publication = env.call(ACTORS[2], "publish", env.key(), env.approved(proposal))
    body = {"publication_id": publication["publication_id"], "expected_binding_revision": 1, "reason": "synthetic exact SKU"}
    plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", body)
    assert plan["can_activate"]
    env.call(ACTORS[2], "activate", env.key(), "price_policy", {
        **body, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]})
    volume = IngredientRequirement(FOOD, volume_ml=Fraction(100))
    mass = IngredientRequirement(FOOD, D(120))
    with Session(env.engine) as db:
        for demand, units in [([mass], ["g"]), ([volume], ["ml"]), ([mass, volume], ["g", "ml"])]:
            result = meal_cost.meal_cost_from_db(db, demand, [], D(100), now=AT, recipe_release_id=RELEASE)
            assert [line.quote.package_unit for line in result.lines] == units
            assert [line.cost for line in result.lines] == [D(30)] * len(demand)
            assert all(line.quote.price_policy_publication_id == publication["publication_id"] for line in result.lines)
