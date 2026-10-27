import json
from decimal import Decimal as D
from fractions import Fraction as F

from nutrition_planning import (
    IngredientQuantity, NutritionPolicy, RecipeCandidate, align_baseline, targets_from_t03, validate_meal,
)


def test_single_person_baseline_is_scaled_then_recalculated(tmp_path):
    catalog = tmp_path / "tfda.json"
    catalog.write_text(json.dumps([{
        "ingredient_id": "sample-A", "state": "cooked", "standard_name": "測試食材",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "sample-A", "source_version": "synthetic"},
    }]), encoding="utf-8")
    policy = NutritionPolicy(catalog_path=catalog, sample_mappings={"food-A": "sample-A"},
                             required_nutrients=("energy_kcal", "protein_g"))
    recipe = RecipeCandidate(mode="ai_generated_menu", ingredients=(IngredientQuantity(
        line_id="line-A", food_id="food-A", sample_id="sample-A", amount_g=D("100"),
        weight_basis="cooked", consumed_as_is=True, cooking_step_g=D("10"),
    ),))
    targets = targets_from_t03({
        "total_n": "1.3", "target": {"energy_kcal": "780", "protein_g": "39"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}},
        "approved_for_adoption": False,
    })
    result = validate_meal(recipe, targets, policy=policy)
    assert result.baseline_check.passed
    assert result.final_recipe is not None
    assert result.final_estimate is not None
    assert result.final_check is not None
    assert result.final_recipe.ingredients[0].amount_g == F(130)
    assert result.final_estimate.totals == {"energy_kcal": F(780), "protein_g": F(39)}
    assert result.final_check.passed
    assert result.nutrition_passed
    assert "target_rules_not_approved" in result.adoption_blockers


def test_database_baseline_can_adjust_only_within_reviewed_bounds(tmp_path):
    catalog = tmp_path / "tfda.json"
    catalog.write_text(json.dumps([{
        "ingredient_id": "sample-A", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "sample-A", "source_version": "synthetic"},
    }]), encoding="utf-8")
    policy = NutritionPolicy(catalog, {"food-A": "sample-A"}, ("energy_kcal", "protein_g"))
    line = IngredientQuantity("line-A", "food-A", "sample-A", D("80"), "cooked", True, D("1"),
                              adjustment_status="approved", adjustable_min_g=D("50"),
                              adjustable_max_g=D("150"))
    recipe = RecipeCandidate("database_menu", (line,), ("recipe-A",), "approved")
    targets = targets_from_t03({"total_n": "1", "target": {"energy_kcal": "600", "protein_g": "30"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = align_baseline(recipe, targets, policy=policy)
    assert result.recipe is not None
    assert F(50) <= F(result.recipe.ingredients[0].amount_g) <= F(150)
    assert result.check.passed


def test_database_baseline_rejects_original_quantity_outside_reviewed_bounds(tmp_path):
    catalog = tmp_path / "tfda.json"
    catalog.write_text(json.dumps([{
        "ingredient_id": "sample-A", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "sample-A", "source_version": "synthetic"},
    }]), encoding="utf-8")
    policy = NutritionPolicy(catalog, {"food-A": "sample-A"}, ("energy_kcal", "protein_g"))
    line = IngredientQuantity("line-A", "food-A", "sample-A", D("100"), "cooked", True, D("1"),
                              adjustment_status="approved", adjustable_min_g=D("110"),
                              adjustable_max_g=D("150"))
    recipe = RecipeCandidate("database_menu", (line,), ("recipe-A",), "approved")
    targets = targets_from_t03({"total_n": "1", "target": {"energy_kcal": "600", "protein_g": "30"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = align_baseline(recipe, targets, policy=policy)
    assert result.recipe is None
    assert result.reason == "invalid_reviewed_range"


def test_original_recipe_servings_are_normalized_before_baseline_alignment(tmp_path):
    catalog = tmp_path / "tfda.json"
    catalog.write_text(json.dumps([{
        "ingredient_id": "sample-A", "state": "cooked",
        "nutrition_per_100g": {"energy_kcal": 600, "protein_g": 30},
        "source": {"provider": "TFDA", "source_id": "sample-A", "source_version": "synthetic"},
    }]), encoding="utf-8")
    policy = NutritionPolicy(catalog, {"food-A": "sample-A"}, ("energy_kcal", "protein_g"))
    line = IngredientQuantity("line-A", "food-A", "sample-A", D("200"), "cooked", True, D("1"),
                              original_servings=D("2"))
    recipe = RecipeCandidate("ai_generated_menu", (line,))
    targets = targets_from_t03({"total_n": "1", "target": {"energy_kcal": "600", "protein_g": "30"},
        "reference_exact": {"energy_kcal": {"numerator": "600", "denominator": "1"},
                            "protein_g": {"numerator": "30", "denominator": "1"}}})
    result = align_baseline(recipe, targets, policy=policy)
    assert result.recipe is not None
    assert result.recipe.ingredients[0].amount_g == F(100)
    assert result.check.passed
