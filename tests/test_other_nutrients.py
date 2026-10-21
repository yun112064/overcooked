"""decisions #40：非必要營養素（碳水、脂肪、糖、鈉、膳食纖維…）烹調後的近似計算。

有烹調換算（產率）時，非必要營養素用 TFDA 生組成、依產率換算重量、保留率視為 1，結果標示「近似」；
熱量與蛋白質維持保留率推估（#19）。TFDA 生組成缺值者仍為未知（不填 0）。

測試的介面（seams）：
1. make_tfda_calculator(cooking_factors=...)：真實 T04 TFDA 資料 + 快取係數 → 營養與 trace；
2. calculate_meal：結果層級的「近似」彙整；
3. meal_results 檢視：每項食材與營養區塊的「近似」標示。
絕不呼叫 LLM：係數一律為測試建立的快取物件。
"""
import unittest
from decimal import Decimal
from fractions import Fraction

from app.meal_calc.types import EstimatedFactor
from app.services import meal_calculation as mc

D = Decimal


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name, grams_per_n=D(grams), **kw)


def cooking(food_id, method, *, yield_factor="0.75", energy="0.95", protein="0.9"):
    return EstimatedFactor(
        estimate_id="est-1", kind="cooking", food_id=food_id, key=method, source="llm_estimated",
        status="llm_estimated", model="gpt-5.4-mini-2026-03-17", prompt_version="p1", schema_version="s1",
        yield_factor=D(yield_factor) if yield_factor is not None else None,
        retention={"energy_kcal": D(energy), "protein_g": D(protein)})


def lookup(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda food_id, method: table.get((food_id, mc.normalize_cooking_method(method)))


class YieldOnlyApproximationTests(unittest.TestCase):
    """雞胸肉 I0402402 生 / 100 g：117 kcal、碳水 0.6、蛋白質 23.3、脂肪 1.9、糖 0、鈉 33、纖維（TFDA 空白）。"""

    def calc(self, **kw):
        return mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-雞胸肉", "roast", **kw)))

    def test_other_nutrients_use_raw_composition_with_retention_one(self):
        [chicken] = self.calc()([(line("雞胸肉", "150", cooking_method="烤"), D("150"))])
        self.assertIsNone(chicken.error)
        self.assertEqual(chicken.nutrients["fat_g"], D("2.85"))
        self.assertEqual(chicken.nutrients["carbohydrate_g"], D("0.9"))
        self.assertEqual(chicken.nutrients["sodium_mg"], D("49.5"))
        self.assertEqual(chicken.nutrients["sugars_g"], D("0"))       # TFDA 實測 0 → 已知 0
        self.assertIsNone(chicken.nutrients["dietary_fiber_g"])       # TFDA 空白 → 仍未知、不填 0
        # 熱量、蛋白質維持保留率推估
        self.assertEqual(chicken.nutrients["energy_kcal"], D("166.725"))
        self.assertEqual(chicken.nutrients["protein_g"], D("31.455"))

    def test_trace_marks_the_approximation(self):
        [chicken] = self.calc()([(line("雞胸肉", "150", cooking_method="烤"), D("150"))])
        self.assertEqual(sorted(chicken.trace["approximate_nutrients"]),
                         ["carbohydrate_g", "fat_g", "sodium_mg", "sugars_g"])
        self.assertEqual(chicken.trace["nutrient_basis"],
                         {k: "yield_only_retention_1" for k in ("carbohydrate_g", "fat_g", "sodium_mg", "sugars_g")})
        # 近似不是 LLM 推估的營養（保留率 1 不來自模型）
        self.assertEqual(sorted(chicken.trace["estimated_nutrients"]), ["energy_kcal", "protein_g"])
        self.assertEqual(chicken.trace["cooked_weight_g"], D("112.5"))

    def test_without_a_yield_other_nutrients_stay_unknown(self):
        [chicken] = self.calc(yield_factor=None)([(line("雞胸肉", "150", cooking_method="烤"), D("150"))])
        self.assertEqual(chicken.nutrients["energy_kcal"], D("166.725"))
        self.assertIsNone(chicken.nutrients["fat_g"])
        self.assertEqual(chicken.trace.get("approximate_nutrients") or [], [])

    def test_uncooked_tfda_values_are_exact_not_approximate(self):
        # 不經烹調（consumed_as_is）時直接用 TFDA 組成：維持原邏輯、不標近似
        [chicken] = mc.tfda_calculator([(line("雞胸肉", "150", consumed_as_is=True), D("150"))])
        self.assertEqual(chicken.nutrients["fat_g"], D("2.85"))
        self.assertEqual(chicken.trace.get("approximate_nutrients") or [], [])


class TomatoEggTests(unittest.TestCase):
    """蕃茄炒蛋類食譜：雞蛋平均值 K01001 100 g ＋ 牛番茄 E7400101 150 g，炒。"""

    def test_other_nutrient_totals_are_known(self):
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-蛋", "stir"),
                                                              cooking("F-番茄", "stir")))
        lines = [line("雞蛋平均值", "100", food_id="F-蛋", cooking_method="炒"),
                 line("牛番茄", "150", food_id="F-番茄", cooking_method="炒")]
        targets = mc.MealTargets(Fraction(1), Fraction("155.325"), Fraction("12.375"))
        result = mc.calculate_meal(lines, targets, mode="ai", calculator=calc)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["final"]["other_nutrients"], {
            "carbohydrate_g": D("7.6"), "fat_g": D("9.05"), "sugars_g": D("3.5"),
            "sodium_mg": D("141"), "dietary_fiber_g": D("1.5")})
        self.assertEqual(result["final"]["missing_nutrients"], {})


class MealApproximationSummaryTests(unittest.TestCase):
    def result(self):
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-雞胸肉", "roast")))
        targets = mc.MealTargets(Fraction(1), Fraction("166.725"), Fraction("31.455"))
        return mc.calculate_meal([line("雞胸肉", "150", cooking_method="烤")], targets, mode="ai",
                                 calculator=calc)

    def test_meal_result_lists_approximate_nutrients(self):
        result = self.result()
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["final"]["other_nutrients"]["fat_g"], D("2.85"))
        [row] = result["ingredients"]
        self.assertEqual(sorted(row["approximate_nutrients"]), ["carbohydrate_g", "fat_g", "sodium_mg", "sugars_g"])
        summary = result["approximations"]
        self.assertTrue(summary["used"])
        self.assertEqual(summary["food_ids"], ["F-雞胸肉"])
        self.assertEqual(sorted(summary["nutrients"]), ["carbohydrate_g", "fat_g", "sodium_mg", "sugars_g"])
        self.assertEqual(summary["basis"], "yield_only_retention_1")

    def test_result_view_labels_approximate_nutrients(self):
        from app.services import meal_results

        result = self.result()
        payload = {"calculation": result, "dishes": [
            {"standard_dish_id": "D1", "dish_name": "烤雞胸", "source": "ai", "recipe_id": None}]}
        result["ingredients"][0]["dish_id"] = "D1"
        nutrition = meal_results._nutrition(payload)
        self.assertEqual(nutrition["approximations"]["label"], "近似")
        self.assertEqual(sorted(nutrition["approximations"]["nutrients"]),
                         ["carbohydrate_g", "fat_g", "sodium_mg", "sugars_g"])
        [dish] = meal_results._dishes(payload)
        [ingredient] = dish["ingredients"]
        self.assertEqual(sorted(ingredient["approximate_nutrients"]),
                         ["carbohydrate_g", "fat_g", "sodium_mg", "sugars_g"])


if __name__ == "__main__":
    unittest.main()
