"""User decisions 2026-11-02: whole-dish portions 0.5–1.0 and exact-gram precision."""
import json
from decimal import Decimal as D
from fractions import Fraction as F

from nutrition_planning import IngredientQuantity, NutritionPolicy, RecipeCandidate, targets_from_t03, validate_meal
from nutrition_planning.portion_fit import FACTORS, candidate_factors, scale_dishes

MICRO = D("0.000001")


def _policy(tmp_path):
    catalog = tmp_path / "tfda.json"
    catalog.write_text(json.dumps([
        {"ingredient_id": "fish", "state": "raw", "nutrition_per_100g": {"energy_kcal": 100, "protein_g": 20},
         "source": {"provider": "TFDA", "source_id": "fish", "source_version": "synthetic"}},
        {"ingredient_id": "veg", "state": "raw", "nutrition_per_100g": {"energy_kcal": 50, "protein_g": 1},
         "source": {"provider": "TFDA", "source_id": "veg", "source_version": "synthetic"}},
        {"ingredient_id": "rice", "state": "cooked", "nutrition_per_100g": {"energy_kcal": 183, "protein_g": 3.1},
         "source": {"provider": "TFDA", "source_id": "rice", "source_version": "synthetic"}}]))
    return NutritionPolicy(catalog, {"f": "fish", "v": "veg", "r": "rice"}, ("energy_kcal", "protein_g"))


def _targets(energy, protein):
    return targets_from_t03({"total_n": "1", "target": {"energy_kcal": energy, "protein_g": protein},
        "reference_exact": {"energy_kcal": {"numerator": energy, "denominator": "1"},
                            "protein_g": {"numerator": protein, "denominator": "1"}}})


def _line(lid, food, sample, grams, servings, **kw):
    return IngredientQuantity(lid, food, sample, D(grams), "raw", True, MICRO, original_servings=servings, **kw)


def test_three_serving_recipe_is_not_blocked_by_non_terminating_grams(tmp_path):
    # 10 g / 3 servings = 3.333… g cannot be written at 0.000001 g; it is
    # rounded by under half a microgram and the ±10% check uses that value.
    recipe = RecipeCandidate("database_menu", (_line("a", "v", "veg", "1800", 3),), ("R",), "approved")
    result = validate_meal(recipe, _targets("300", "6"), policy=_policy(tmp_path))
    assert result.nutrition_passed
    recipe = RecipeCandidate("database_menu", (_line("a", "v", "veg", "1801", 3),), ("R",), "approved")
    result = validate_meal(recipe, _targets("300", "6"), policy=_policy(tmp_path))
    assert result.nutrition_passed
    assert abs(F(result.final_recipe.ingredients[0].amount_g) - F(1801, 3)) * 2 <= F(1, 10 ** 6)


def test_whole_dish_factor_keeps_proportions_and_stays_within_half_to_one(tmp_path):
    policy = _policy(tmp_path)
    lines = (_line("fish", "f", "fish", "400", 2), _line("veg1", "v", "veg", "200", 2),
             _line("veg2", "v", "veg", "300", 3),
             IngredientQuantity("staple", "r", "rice", D("160"), "cooked", True, D("1"),
                                adjustment_status="approved", adjustable_min_g=D("100"),
                                adjustable_max_g=D("250"), original_servings=1))
    recipe = RecipeCandidate("database_menu", lines, ("R1", "R2"), "approved")
    line_dish = {"fish": "R1", "veg1": "R1", "veg2": "R2"}
    targets = _targets("700", "30")
    assert not validate_meal(recipe, targets, policy=policy).nutrition_passed   # 40 g protein from fish alone
    factors = next(candidate_factors(recipe, line_dish, targets, policy))
    assert set(factors) == {"R1", "R2"} and all(f in FACTORS for f in factors.values())
    scaled = scale_dishes(recipe, line_dish, factors)
    by_id = {line.line_id: line for line in scaled.ingredients}
    assert F(by_id["fish"].amount_g) / 400 == F(by_id["veg1"].amount_g) / 200 == factors["R1"]
    assert by_id["staple"].amount_g == D("160")
    result = validate_meal(scaled, targets, policy=policy)
    assert result.nutrition_passed and result.final_check.passed


def test_no_factor_below_half_is_ever_offered(tmp_path):
    policy = _policy(tmp_path)
    recipe = RecipeCandidate("database_menu", (_line("fish", "f", "fish", "1000", 1),), ("R1",), "approved")
    # 0.5 serving still gives 100 g protein against a 30 g target: nothing fits.
    assert list(candidate_factors(recipe, {"fish": "R1"}, _targets("700", "30"), policy)) == []
    assert min(FACTORS) == F(1, 2) and max(FACTORS) == 1
