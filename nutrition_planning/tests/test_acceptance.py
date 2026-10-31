import json
from dataclasses import replace
from pathlib import Path
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from nutrition_planning import (IngredientQuantity, NutritionEstimate, NutritionPolicy,
    RecipeCandidate, CookingEvidence, check_tolerance, targets_from_t03, validate_meal, calculate_nutrition)


@pytest.mark.parametrize("n,energy,protein", [
    ("0.7", "420", "21"), ("0.85", "510", "25.5"), ("1", "600", "30"),
    ("1.15", "690", "34.5"), ("1.3", "780", "39"),
])
def test_five_portions_scale_without_changing_the_target(tmp_path, n, energy, protein):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "s", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "s", "source_version": "test"}}]))
    policy = NutritionPolicy(catalog, {"food": "s"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "line", "food", "s", D("100"), "cooked", True, D("1"), original_servings=1),))
    targets = targets_from_t03({"total_n": n, "target": {"energy_kcal": energy, "protein_g": protein},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = validate_meal(recipe, targets, policy=policy)
    assert result.final_estimate is not None
    assert result.final_estimate.totals == {"energy_kcal": F(energy), "protein_g": F(protein)}
    assert result.nutrition_passed


@pytest.mark.parametrize("energy,protein,passes", [
    ("540", "30", True), ("660", "30", True), ("600", "27", True), ("600", "33", True),
    ("539.999999", "30", False), ("660.000001", "30", False),
    ("600", "26.999999", False), ("600", "33.000001", False),
])
def test_independent_energy_and_protein_boundaries(energy, protein, passes):
    estimate = NutritionEstimate({"energy_kcal": F(energy), "protein_g": F(protein)}, (), ())
    assert check_tolerance(estimate, {"energy_kcal": F(600), "protein_g": F(30)}).passed is passes


def test_missing_nutrition_does_not_become_zero(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "s", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": None},
        "source": {"provider": "TFDA", "source_id": "s", "source_version": "test"}}]))
    policy = NutritionPolicy(catalog, {"food": "s"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "line", "food", "s", D("100"), "cooked", True, D("1")),))
    result = calculate_nutrition(recipe, policy=policy)
    assert result.totals["protein_g"] is None
    assert result.missing
    assert not check_tolerance(result, {"energy_kcal": F(600), "protein_g": F(30)}).passed


def test_real_tfda_cooked_sample_is_not_adjusted_twice():
    catalog = Path(__file__).resolve().parents[2] / "nutrition_model/data/ingredient_master.json"
    policy = NutritionPolicy(catalog, {"food-rice": "A0550601"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "rice", "food-rice", "A0550601", D("100"), "cooked", True, D("1")),))
    actual = calculate_nutrition(recipe, policy=policy)
    assert actual.totals == {"energy_kcal": F(183), "protein_g": F("3.1")}
    assert actual.sources[0]["sample_id"] == "A0550601"
    assert not actual.missing


def test_raw_sample_requires_reviewed_retention_and_calculates_oil_too(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([
        {"ingredient_id": "raw", "state": "raw", "nutrition_per_100g": {"energy_kcal": 200, "protein_g": 20},
         "source": {"provider": "TFDA", "source_id": "raw", "source_version": "test"}},
        {"ingredient_id": "oil", "state": "raw", "nutrition_per_100g": {"energy_kcal": 900, "protein_g": 0},
         "source": {"provider": "TFDA", "source_id": "oil", "source_version": "test"}},
    ]))
    ev = CookingEvidence("evidence-1", "raw", "steam", retention={"energy_kcal": "0.9", "protein_g": "0.8"},
                         source="synthetic-review", source_version="test-v1", conditions={"juices_consumed": True})
    food = IngredientQuantity("food", "food-id", "raw", D("100"), "raw", False, D("1"), "steam", ev,
                              cooking_context={"juices_consumed": True})
    oil = IngredientQuantity("oil", "oil-id", "oil", D("10"), "raw", True, D("1"))
    recipe = RecipeCandidate("ai_generated_menu", (food, oil))
    policy = NutritionPolicy(catalog, {"food-id": "raw", "oil-id": "oil"},
                             ("energy_kcal", "protein_g"), {"evidence-1": ev})
    result = calculate_nutrition(recipe, policy=policy)
    assert result.totals == {"energy_kcal": F(270), "protein_g": F(16)}
    assert len(result.sources) == 2
    snapshot = result.sources[0]["cooking_evidence"]
    assert isinstance(snapshot, dict)
    assert snapshot["retention"]["protein_g"] == "0.8"
    assert snapshot["source_version"] == "test-v1"
    not_approved = calculate_nutrition(recipe, policy=NutritionPolicy(
        catalog, policy.sample_mappings, policy.required_nutrients))
    assert not_approved.totals["protein_g"] is None
    assert not_approved.missing


def test_candidate_cannot_change_an_approved_evidence_coefficient(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "raw", "state": "raw",
        "nutrition_per_100g": {"energy_kcal": 200, "protein_g": 20},
        "source": {"provider": "TFDA", "source_id": "raw", "source_version": "test"}}]))
    approved = CookingEvidence("evidence-1", "raw", "steam",
                               retention={"energy_kcal": "0.9", "protein_g": "0.8"},
                               source="synthetic-review", source_version="test-v1", conditions={"juices_consumed": True})
    forged = replace(approved, retention={"energy_kcal": "1", "protein_g": "1"})
    line = IngredientQuantity("food", "food-id", "raw", D("100"), "raw", False, D("1"), "steam", forged,
                              cooking_context={"juices_consumed": True})
    recipe = RecipeCandidate("ai_generated_menu", (line,))
    policy = NutritionPolicy(catalog, {"food-id": "raw"}, ("energy_kcal", "protein_g"),
                             {"evidence-1": approved})
    result = calculate_nutrition(recipe, policy=policy)
    assert result.totals == {"energy_kcal": F(180), "protein_g": F(16)}


def test_approved_retention_must_match_actual_cooking_conditions(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "raw", "state": "raw",
        "nutrition_per_100g": {"energy_kcal": 200, "protein_g": 20},
        "source": {"provider": "TFDA", "source_id": "raw", "source_version": "test"}}]))
    approved = CookingEvidence("evidence-1", "raw", "steam", retention={"energy_kcal": "0.9", "protein_g": "0.8"},
        source="synthetic-review", source_version="test-v1", conditions={"juices_consumed": True})
    line = IngredientQuantity("food", "food-id", "raw", D("100"), "raw", False, D("1"), "steam", approved,
                              cooking_context={"juices_consumed": False})
    policy = NutritionPolicy(catalog, {"food-id": "raw"}, ("energy_kcal", "protein_g"), {"evidence-1": approved})
    result = calculate_nutrition(RecipeCandidate("ai_generated_menu", (line,)), policy=policy)
    assert result.totals["protein_g"] is None
    assert result.missing


def test_cooked_weight_cannot_bypass_raw_sample_retention():
    catalog = Path(__file__).resolve().parents[2] / "nutrition_model/data/ingredient_master.json"
    evidence = CookingEvidence("evidence", "A0100101", None, yield_factor="0.5",  # type: ignore
        source="test", source_version="1", conditions={"juices_consumed": True})
    line = IngredientQuantity("line", "food", "A0100101", D("100"), "cooked", True, D("1"),
                              evidence=evidence, cooking_context={"juices_consumed": True})
    policy = NutritionPolicy(catalog, {"food": "A0100101"}, ("energy_kcal", "protein_g"),
                             {"evidence": evidence})
    result = calculate_nutrition(RecipeCandidate("ai_generated_menu", (line,)), policy=policy)
    assert result.totals["energy_kcal"] is None
    assert result.totals["protein_g"] is None
    assert result.missing


def test_string_false_cannot_mark_raw_food_as_directly_eaten():
    catalog = Path(__file__).resolve().parents[2] / "nutrition_model/data/ingredient_master.json"
    policy = NutritionPolicy(catalog, {"food": "A0100101"}, ("energy_kcal", "protein_g"))
    line = IngredientQuantity("line", "food", "A0100101", D("100"), "raw", "false", D("1"))  # type: ignore
    result = calculate_nutrition(RecipeCandidate("ai_generated_menu", (line,)), policy=policy)
    assert result.totals["protein_g"] is None
    assert result.missing


def test_missing_original_servings_cannot_be_assumed_to_be_one(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "s", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "s", "source_version": "test"}}]))
    policy = NutritionPolicy(catalog, {"food": "s"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "line", "food", "s", D("100"), "cooked", True, D("1")),))
    targets = targets_from_t03({"total_n": "1", "target": {"energy_kcal": "600", "protein_g": "30"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = validate_meal(recipe, targets, policy=policy)
    assert not result.nutrition_passed
    assert result.reason == "missing_original_servings"


def test_cooking_unit_rounding_requires_final_recalculation(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "s", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "s", "source_version": "test"}}]))
    policy = NutritionPolicy(catalog, {"food": "s"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "line", "food", "s", D("100"), "cooked", True, D("40"), original_servings=1),))
    targets = targets_from_t03({"total_n": "1.3", "target": {"energy_kcal": "780", "protein_g": "39"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = validate_meal(recipe, targets, policy=policy)
    assert result.baseline_check.passed
    assert result.final_estimate is not None
    assert result.final_estimate.totals == {"energy_kcal": F(720), "protein_g": F(36)}
    assert result.nutrition_passed


def test_final_rounding_outside_tolerance_is_not_accepted(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"ingredient_id": "s", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "s", "source_version": "test"}}]))
    policy = NutritionPolicy(catalog, {"food": "s"}, ("energy_kcal", "protein_g"))
    recipe = RecipeCandidate("ai_generated_menu", (IngredientQuantity(
        "line", "food", "s", D("100"), "cooked", True, D("80"), original_servings=1),))
    targets = targets_from_t03({"total_n": "1.3", "target": {"energy_kcal": "780", "protein_g": "39"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = validate_meal(recipe, targets, policy=policy)
    assert result.baseline_check.passed
    assert result.final_estimate is not None
    assert result.final_estimate.totals == {"energy_kcal": F(960), "protein_g": F(48)}
    assert not result.nutrition_passed
