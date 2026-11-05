"""User decisions 2026-11-02/03: whole-dish portions follow each participant's target; exact grams."""
import json
from decimal import Decimal as D
from fractions import Fraction as F
from typing import cast

from nutrition_planning import IngredientQuantity, NutritionPolicy, RecipeCandidate, targets_from_t03, validate_meal
from nutrition_planning.portion_fit import FACTORS, UNIFORM_FACTORS, candidate_factors, scale_dishes

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
    assert result.final_recipe is not None
    assert abs(F(result.final_recipe.ingredients[0].amount_g) - F(1801, 3)) * 2 <= F(1, 10 ** 6)


def test_whole_dish_factor_keeps_proportions_and_stays_in_range(tmp_path):
    policy = _policy(tmp_path)
    lines = (_line("fish", "f", "fish", "400", 2), _line("veg1", "v", "veg", "200", 2),
             _line("veg2", "v", "veg", "300", 3),
             IngredientQuantity("staple", "r", "rice", D("160"), "cooked", True, D("1"),
                                adjustment_status="approved", adjustable_min_g=D("100"),
                                adjustable_max_g=D("250"), original_servings=1))
    recipe = RecipeCandidate("database_menu", lines, ("R1", "R2"), "approved")
    line_dish = {"fish": "R1", "veg1": "R1", "veg2": "R2"}
    targets = _targets("400", "30")
    # Over 110% energy even with the least rice (100 g); only smaller dishes fit.
    assert not validate_meal(recipe, targets, policy=policy).nutrition_passed
    factors = next(candidate_factors(recipe, line_dish, targets, policy))
    assert set(factors) == {"R1", "R2"} and all(f in FACTORS or f in UNIFORM_FACTORS for f in factors.values())
    scaled = scale_dishes(recipe, line_dish, factors)
    by_id = {line.line_id: line for line in scaled.ingredients}
    assert F(by_id["fish"].amount_g) / 400 == F(by_id["veg1"].amount_g) / 200 == factors["R1"]
    assert by_id["staple"].amount_g == D("160")
    result = validate_meal(scaled, targets, policy=policy)
    assert result.nutrition_passed and result.final_check is not None and result.final_check.passed


def test_factor_range_and_impossible_balance_still_fails(tmp_path):
    policy = _policy(tmp_path)
    recipe = RecipeCandidate("database_menu", (_line("fish", "f", "fish", "1000", 1),), ("R1",), "approved")
    # Fish alone has 5 kcal per 1 g protein: at least 27 g protein means 135 kcal, over 110 kcal.
    assert list(candidate_factors(recipe, {"fish": "R1"}, _targets("100", "30"), policy)) == []
    assert min(UNIFORM_FACTORS) == F(1, 100) and max(UNIFORM_FACTORS) == 2
    assert min(FACTORS) == F(1, 10) and max(FACTORS) == 2


def test_small_child_gets_same_small_share_of_every_dish(tmp_path):
    """2026-11-03: a menu several times a child's target is portioned down, not rejected."""
    policy = _policy(tmp_path)
    lines = (_line("fish", "f", "fish", "1000", 1), _line("veg", "v", "veg", "2000", 1),
             IngredientQuantity("staple", "r", "rice", D("160"), "cooked", True, D("1"),
                                adjustment_status="approved", adjustable_min_g=D("100"),
                                adjustable_max_g=D("250"), original_servings=1))
    recipe = RecipeCandidate("database_menu", lines, ("R1", "R2"), "approved")
    line_dish = {"fish": "R1", "veg": "R2"}
    targets = _targets("460", "13")   # menu is 2000+ kcal; a 0.5 floor could never fit
    assert not validate_meal(recipe, targets, policy=policy).nutrition_passed
    factors = next(candidate_factors(recipe, line_dish, targets, policy))
    assert factors["R1"] == factors["R2"] < F(1, 2)
    result = validate_meal(scale_dishes(recipe, line_dish, factors), targets, policy=policy)
    assert result.nutrition_passed


def test_person_shares_of_each_dish_add_up_to_one():
    """2026-11-03：每道菜整份＝1，所有人（含便當）分到的份量加總要剛好 1。"""
    from nutrition_planning.integration import _shares_summing_to_one
    for shares in ([F(5108, 9724), F(4616, 9724)], [F(1, 3)] * 3, [F(1, 6), F(1, 6), F(2, 3)], [F(1)]):
        shown = _shares_summing_to_one(shares)
        assert sum(shown) == 1
        assert all(abs(s - e) < F(1, 100) for s, e in zip(shown, shares))


def test_seven_nutrients_show_known_part_and_missing_names_never_zero():
    """2026-11-03：畫面顯示七種營養素；缺值列出已知部分與缺資料的食材，水的空白視為 0。"""
    from nutrition_planning.integration import DatabaseMenuResult, PreparedRecipe
    full = dict(energy_kcal=F(100), protein_g=F(5), fat_g=F(2), carbohydrate_g=F(10), sugars_g=F(1),
                sodium_mg=F(50), dietary_fiber_g=F(3))
    result = DatabaseMenuResult(
        _targets('600', '30'), PreparedRecipe(None, ()), None,
        line_labels={'a': {'recipe_id': 'R', 'name': '雞肉'}, 'b': {'recipe_id': 'R', 'name': '醬油'},
                     'c': {'recipe_id': 'R', 'name': '水'}},
        line_nutrients={'a': full, 'b': {**full, 'fat_g': None},
                        'c': {k: (F(0) if k in ('energy_kcal', 'protein_g') else None) for k in full}})
    shown = cast(dict, result.all_nutrients)
    assert shown['totals']['sodium_mg'] == '100'            # water's blank sodium counts as 0
    assert shown['totals']['fat_g'] is None                  # soy sauce has no fat value: not 0
    assert shown['known_partial']['fat_g'] == '2'
    assert shown['missing_by_nutrient']['fat_g'] == ['醬油']
