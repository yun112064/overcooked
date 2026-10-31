import json
from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

import pytest
from sqlalchemy import (Boolean, Column, Integer, MetaData, Numeric, String,
                        Table, create_engine, update)

from nutrition_target import NutritionTargetService
from nutrition_planning import NutritionPolicy, PlanningInputError, calculate_nutrition
from nutrition_planning.integration import (
    RecipeDataUnavailable, RecipeLineProfile, load_database_recipe_rows,
    prepare_database_recipe, recipe_line_digest, validate_database_menu,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def menu(tmp_path):
    # Actual SQL round trips using the T01/T11 column names/types. PostgreSQL
    # deployment/migrations are a separate integration check, not claimed here.
    engine = create_engine("sqlite://")
    meta = MetaData()
    recipes = Table("recipes", meta, Column("recipe_id", String, primary_key=True),
                    Column("original_servings", Numeric(10, 3)), Column("review_status", String),
                    Column("is_eligible", Boolean))
    lines = Table("recipe_ingredients", meta,
                  Column("recipe_ingredient_id", String, primary_key=True),
                  Column("recipe_id", String), Column("sequence", Integer), Column("food_id", String),
                  Column("canonical_name", String),
                  Column("weight_g", Numeric(16, 6)), Column("weight_status", String),
                  Column("unit", String), Column("is_required", Boolean),
                  Column("adjustment_status", String), Column("adjustable_min", Numeric(16, 6)),
                  Column("adjustable_max", Numeric(16, 6)))
    meta.create_all(engine)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([
        {"ingredient_id": sample, "standard_name": sample, "state": "cooked",
         "nutrition_per_100g": {"energy_kcal": energy, "protein_g": protein},
         "source": {"provider": "TFDA", "source_id": sample, "source_version": "synthetic-integration"}}
        for sample, energy, protein in [("main", "687.14364", "30.8"), ("oil", "900", "0"),
                                        ("salt", "0", "0")]]), encoding="utf-8")
    policy = NutritionPolicy(catalog, {"FOOD000001": "main", "FOOD000002": "oil", "FOOD000003": "salt"},
                             ("energy_kcal", "protein_g"))
    target_service = NutritionTargetService(ROOT / "nutrition_target/rules/hpa-dri8-mvp-0.3.0.json")
    participations = [{"participation_id": "dinner", "kind": "dining", "meal": "dinner", "n": "1.3",
        "member": {"member_id": "adult", "age_years": 30, "equation_sex": "M", "weight_kg": "70",
                   "activity_category": "sedentary", "calculation_date": "2026-10-30", "goal": "maintain"}}]
    with engine.begin() as db:
        db.execute(recipes.insert(), [dict(recipe_id="R1", original_servings=2, review_status="approved", is_eligible=True),
                                     dict(recipe_id="unselected", original_servings=1, review_status="approved", is_eligible=True)])
        db.execute(lines.insert(), [dict(recipe_ingredient_id=lid, recipe_id="R1", sequence=i,
            food_id=f"FOOD{i:06}", canonical_name=lid, weight_g=D(weight), weight_status="source_explicit", unit="g",
            is_required=True, adjustment_status="not_reviewed")
            for i, (lid, weight) in enumerate([("main", "200"), ("oil", "20"), ("salt", "4")], 1)])
        rows = load_database_recipe_rows(db, ["R1"])
        profiles = {r["recipe_ingredient_id"]: RecipeLineProfile(
            r["food_id"], recipe_line_digest(rows.recipes[0], r), "cooked", True,
            D("0.1"), "synthetic-reviewed-unit", "v1", review_status="approved") for r in rows.ingredients}
        yield db, recipes, lines, rows, profiles, policy, target_service, participations
    engine.dispose()


def test_db_t03_t12_bridge_counts_all_lines_and_preserves_exact_targets(menu):
    db, _, _, _, profiles, policy, service, participations = menu
    result = validate_database_menu(db, ["R1"], participations=participations, nutrition_source='legacy_tfda',
                                   target_service=service, policy=policy, profiles=profiles)
    assert not result.preparation.issues
    assert result.validation is not None and result.validation.final_estimate is not None
    assert result.validation.final_recipe is not None
    assert result.validation.nutrition_passed
    assert result.targets.target == {"energy_kcal": F("1010.286732"), "protein_g": F("40.04")}
    assert result.validation.final_estimate.totals == result.targets.target
    assert {r.line_id: r.amount_g for r in result.validation.final_recipe.ingredients} == {
        "main": F(130), "oil": F(13), "salt": F("2.6")}
    assert result.final_food_ids == ("FOOD000001", "FOOD000002", "FOOD000003")
    assert set(result.validation.adoption_blockers) == {"target_rules_not_approved", "nutrition_policy_not_approved"}
    assert "reference_exact" in result.targets.snapshot
    assert len(result.validation.final_estimate.sources) == 3


def test_missing_mapping_and_profile_are_reported_without_partial_calculation(menu):
    db, _, _, _, profiles, policy, service, participations = menu
    policy = replace(policy, sample_mappings={"FOOD000001": "main"})
    result = validate_database_menu(db, ["R1"], participations=participations, nutrition_source='legacy_tfda',
                                   target_service=service, policy=policy, profiles={"main": profiles["main"]})
    assert result.validation is None and result.final_food_ids == ()
    assert {(i.line_id, i.code) for i in result.preparation.issues} >= {
        ("oil", "tfda_mapping_missing"), ("salt", "tfda_mapping_missing"),
        ("oil", "reviewed_line_profile_missing"), ("salt", "reviewed_line_profile_missing")}
    assert all(i.recipe_id == "R1" and i.input_digest for i in result.preparation.issues)


def test_changed_db_line_invalidates_old_reviewed_profile(menu):
    db, _, lines, _, profiles, policy, _, _ = menu
    db.execute(update(lines).where(lines.c.recipe_ingredient_id == "oil").values(weight_g=200))
    result = prepare_database_recipe(load_database_recipe_rows(db, ["R1"]), policy=policy, profiles=profiles)
    assert result.recipe is None
    assert any(i.line_id == "oil" and i.code == "line_profile_unreviewed_or_stale" for i in result.issues)


@pytest.mark.parametrize("field,value,code", [
    ("review_status", "pending", "line_profile_unreviewed_or_stale"),
    ("cooking_step_g", None, "missing_or_invalid_value"),
    ("cooking_step_g", 0, "missing_or_invalid_value"),
    ("weight_basis", "unknown", "weight_basis_unknown"),
    ("consumed_as_is", "true", "consumed_as_is_unknown"),
    ("food_id", "different", "line_profile_unreviewed_or_stale"),
    ("evidence_id", "invented", "approved_cooking_evidence_missing"),
])
def test_unreviewed_missing_or_mismatched_data_never_get_defaults(menu, field, value, code):
    _, _, _, rows, profiles, policy, _, _ = menu
    profiles = {**profiles, "oil": replace(profiles["oil"], **{field: value})}
    result = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert result.recipe is None and any(i.code == code for i in result.issues)


def test_unverified_spoon_weight_needs_explicit_reviewed_grams(menu):
    db, _, lines, _, profiles, policy, _, _ = menu
    db.execute(update(lines).where(lines.c.recipe_ingredient_id == "oil").values(
        unit="大匙", weight_g=None, weight_status="unverified_conversion",
        adjustment_status="approved", adjustable_min=1, adjustable_max=2))
    rows = load_database_recipe_rows(db, ["R1"])
    row = next(r for r in rows.ingredients if r["recipe_ingredient_id"] == "oil")
    profiles["oil"] = replace(profiles["oil"], input_digest=recipe_line_digest(rows.recipes[0], row))
    result = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert any(i.code == "reviewed_gram_conversion_missing" for i in result.issues)
    profiles["oil"] = replace(profiles["oil"], reviewed_weight_g=20, reviewed_min_g=10, reviewed_max_g=30)
    result = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert not result.issues
    assert result.recipe is not None
    oil = next(r for r in result.recipe.ingredients if r.line_id == "oil")
    assert (oil.amount_g, oil.adjustable_min_g, oil.adjustable_max_g) == (20, 10, 30)


def test_missing_recipe_is_distinct_from_database_failure(menu):
    db, _, _, _, profiles, policy, _, _ = menu
    result = prepare_database_recipe(load_database_recipe_rows(db, ["missing"]), policy=policy, profiles=profiles)
    assert result.issues[0].code == "recipe_not_found"
    with create_engine("sqlite://").connect() as empty_db:
        with pytest.raises(RecipeDataUnavailable):
            load_database_recipe_rows(empty_db, ["R1"])
    with pytest.raises(PlanningInputError):
        load_database_recipe_rows(db, ["R1", "R1"])


def test_real_tfda_catalog_sample_reads_through_bridge_without_double_conversion(menu):
    _, _, _, rows, profiles, policy, _, _ = menu
    policy = replace(policy, catalog_path=ROOT / "nutrition_model/data/ingredient_master.json",
                     sample_mappings={"FOOD000001": "A0550601"})
    # A single selected line from a complete one-ingredient fixture, not dropping
    # any line from a real selected recipe.
    rows = replace(rows, ingredients=(rows.ingredients[0],))
    result = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert result.recipe is not None
    estimate = calculate_nutrition(result.recipe, policy=policy)
    assert estimate.totals == {"energy_kcal": F(366), "protein_g": F("6.2")}
    source = estimate.sources[0]['source']
    assert isinstance(source, dict) and source['provider'] == 'TFDA'


def test_complete_db_target_t04_llm_and_t12_calculation(menu, monkeypatch):
    import importlib
    from nutrition_planning.t04_bridge import T04CookingResult
    db, _, _, rows, profiles, policy, service, participations = menu
    monkeypatch.syspath_prepend(str(ROOT / 'nutrition_model'))
    t04 = importlib.import_module('cooked_nutrition_service')
    catalog = json.loads(policy.catalog_path.read_text(encoding='utf-8'))
    catalog[0]['state'] = 'raw'
    catalog[0]['nutrition_per_100g'] = {'energy_kcal': '858.92955', 'protein_g': '38.5'}
    policy.catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
    raw = {**catalog[0], 'nutrition_per_100g': {k: float(v) for k, v in catalog[0]['nutrition_per_100g'].items()}}
    context = {'juices_consumed': False}
    profiles['main'] = replace(profiles['main'], weight_basis='raw', consumed_as_is=False,
                               cooking_method='烤', cooking_context=context)
    monkeypatch.setattr(t04.yields, 'get_yield_references', lambda *args, **kwargs: [
        dict(reference_id='integration-reference', match_type='analogy')])
    def estimator(payload):
        return dict(yield_factor=0.75, reference_ids=['integration-reference'],
            nutrient_retention_factors={k: 0.8 for k in t04.NUTRIENTS},
            assumptions=['Synthetic end-to-end example'], missing_information=[])
    result = t04.calculate_cooking('測試主食材', 200, '烤', [raw], lambda _: raw,
                                  cooking_context=context, estimator=estimator)
    receipt = T04CookingResult(profiles['main'].input_digest, 'FOOD000001', 'raw', '200',
                              '烤', context, 'T04-synthetic+model-v1', result)
    final = validate_database_menu(db, ['R1'], participations=participations, nutrition_source='legacy_tfda',
        target_service=service, policy=policy, profiles=profiles, t04_results={'main': receipt})
    assert final.validation is not None and final.validation.final_estimate is not None
    assert final.validation.final_recipe is not None
    assert final.validation.nutrition_passed
    assert final.validation.final_estimate.totals == {'energy_kcal': F('1010.286732'), 'protein_g': F('40.04')}
    main = next(s for s in final.validation.final_estimate.sources if s['line_id'] == 'main')
    evidence = main['cooking_evidence']
    assert isinstance(evidence, dict) and evidence['is_estimated']
    assert evidence['yield_factor'] == F(3, 4)
    assert len(final.validation.final_recipe.ingredients) == 3
    invalid = replace(receipt, input_weight_g='wrong')
    blocked = validate_database_menu(db, ['R1'], participations=participations, nutrition_source='legacy_tfda',
        target_service=service, policy=policy, profiles=profiles, t04_results={'main': invalid})
    assert blocked.validation is None and blocked.preparation.issues


def test_registry_is_used_by_database_menu_entry_and_conflicts_do_not_override_policy(menu, tmp_path):
    from nutrition_model.food_id_mapping import build_food_id_mappings
    db, _, _, rows, profiles, policy, service, participations = menu
    document = build_food_id_mappings([
        dict(food_id=r['food_id'],names=[r['canonical_name']]) for r in rows.ingredients],policy.catalog_path)
    registry = tmp_path/'mapping.json'
    registry.write_text(json.dumps(document),encoding='utf-8')
    blank = replace(policy,sample_mappings={})
    final = validate_database_menu(db,['R1'],participations=participations,target_service=service, nutrition_source='legacy_tfda',
        policy=blank,profiles=profiles,food_mapping_path=registry)
    assert final.validation is not None and final.validation.nutrition_passed
    assert final.final_food_ids == ('FOOD000001','FOOD000002','FOOD000003')
    conflict = replace(policy,sample_mappings={'FOOD000001':'different-sample'})
    with pytest.raises(PlanningInputError,match='conflicts'):
        validate_database_menu(db,['R1'],participations=participations,target_service=service, nutrition_source='legacy_tfda',
            policy=conflict,profiles=profiles,food_mapping_path=registry)
