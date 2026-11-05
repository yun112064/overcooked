"""食材修正：攝取量（config/recipe_intake_adjustments.json，只影響營養）與
工作簿換算錯誤（config/recipe_weight_corrections.json，營養、缺料、價格都用修正後克數）。"""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.services.meal_calculation import (IngredientLine, IngredientNutrition, _evaluate, intake_adjustments,
                                           load_recipe_lines, weight_corrections)
from db_support import migrate_fresh, requires_db, seed_foods, session_factory, truncate_all

# 每克鈉（mg）：食鹽 400、鹹蛋 17、鹹蛋黃 4；FOOD_X 沒有鈉資料
SODIUM_PER_G = {"SALT": Decimal(400), "EGG": Decimal(17), "YOLK": Decimal(4), "FOOD_X": None}


def calculator(pairs):
    out = []
    for ln, grams in pairs:
        per_g = SODIUM_PER_G[ln.food_id]
        out.append(IngredientNutrition({"energy_kcal": Decimal(0), "protein_g": Decimal(0),
                                        "sodium_mg": None if per_g is None else per_g * grams},
                                       {"food_id": ln.food_id, "grams": str(grams)}))
    return out


def line(food_id, parts=None):
    return IngredientLine(food_id=food_id, name=food_id, grams_per_n=Decimal(10), intake_parts=parts)


class IntakeCalculationTests(unittest.TestCase):
    def test_blanching_salt_counts_only_the_retained_fraction(self):
        rows, totals, _, _ = _evaluate([line("SALT", (("SALT", "食鹽", Decimal("0.1")),))], [Decimal(10)], calculator)
        self.assertEqual(rows[0].nutrients["sodium_mg"], Decimal(400))  # 10 g × 0.1 × 400
        self.assertEqual(totals["sodium_mg"], 400)
        self.assertEqual(rows[0].trace["intake_adjustment"]["parts"][0]["grams"], "1.0")

    def test_salted_egg_can_be_counted_as_yolk_plus_some_white(self):
        parts = (("YOLK", "鹹蛋黃", Decimal("0.3")), ("EGG", "鹹蛋", Decimal("0.125")))
        rows, totals, _, _ = _evaluate([line("EGG", parts), line("SALT")], [Decimal(120), Decimal(1)], calculator)
        # 蛋黃 36 g × 4 ＋ 蛋 15 g × 17 ＝ 399；未修正的食鹽照算 400
        self.assertEqual(rows[0].nutrients["sodium_mg"], Decimal(399))
        self.assertEqual(rows[1].nutrients["sodium_mg"], Decimal(400))
        self.assertEqual(totals["sodium_mg"], 799)

    def test_zero_ratio_part_counts_as_zero_without_asking_the_calculator(self):
        # 重複列出的高湯（比例 0）：即使該樣本缺某項營養，也是 0、不是未知，不能讓整餐變未知
        rows, totals, missing, _ = _evaluate([line("FOOD_X", (("FOOD_X", "高湯", Decimal(0)),)), line("SALT")],
                                             [Decimal(500), Decimal(1)], calculator)
        self.assertEqual(rows[0].nutrients["sodium_mg"], 0)
        self.assertEqual(rows[0].nutrients["energy_kcal"], 0)
        self.assertEqual(totals["sodium_mg"], 400)
        self.assertNotIn("sodium_mg", missing)

    def test_unknown_part_makes_the_ingredient_unknown(self):
        parts = (("YOLK", "鹹蛋黃", Decimal("0.3")), ("FOOD_X", "未知", Decimal("0.1")))
        rows, totals, missing, partial = _evaluate([line("EGG", parts)], [Decimal(120)], calculator)
        self.assertIsNone(rows[0].nutrients["sodium_mg"])
        self.assertIsNone(totals["sodium_mg"])
        self.assertEqual(missing["sodium_mg"], ["EGG"])


def test_intake_policy_loads_parts_by_recipe_ingredient(tmp_path):
    path = tmp_path / "intake.json"
    path.write_text(json.dumps({"adjustments": {"RI-TEST": {
        "parts": [{"food_id": "FOOD-TEST", "name": "合成食材", "ratio": "0.3"}],
    }}}), encoding="utf-8")
    assert intake_adjustments(str(path)) == {"RI-TEST": (("FOOD-TEST", "合成食材", Decimal("0.3")),)}


def test_weight_policy_loads_servings_and_grams(tmp_path):
    path = tmp_path / "weight.json"
    path.write_text(json.dumps({
        "recipes": {"R-TEST": {"servings": "4"}},
        "ingredients": {"RI-TEST": {"weight_g": "300"}},
    }), encoding="utf-8")
    assert weight_corrections(str(path)) == ({"R-TEST": Decimal(4)}, {"RI-TEST": Decimal(300)})


@requires_db
class WeightCorrectionLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from test_t11_recipe_retrieval import seed_recipes
        truncate_all()
        seed_foods()
        self.factory = session_factory()
        seed_recipes(self.factory)

    def test_corrected_weight_and_servings_replace_the_workbook_values(self):
        # 合成食譜 R01：2 人份、每項 100 g；修正為 4 人份、第一項 30 g
        with patch("app.services.meal_calculation.weight_corrections",
                   return_value=({"R01": Decimal(4)}, {"R01-I1": Decimal(30)})), self.factory() as db:
            first, second = load_recipe_lines(db, "R01")
        self.assertEqual((first.grams_per_n, first.weight_source), (Decimal("7.5"), "corrected"))
        self.assertEqual((second.grams_per_n, second.weight_source), (Decimal(25), "recipe_grams"))

    def test_without_corrections_the_workbook_values_are_used(self):
        with patch("app.services.meal_calculation.weight_corrections", return_value=({}, {})), self.factory() as db:
            first, _ = load_recipe_lines(db, "R01")
        self.assertEqual((first.grams_per_n, first.weight_source), (Decimal(50), "recipe_grams"))


if __name__ == "__main__":
    unittest.main()
