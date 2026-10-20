"""T12 基準營養對齊、n 倍與烹調營養計算。

純計算以假的 TFDA 表（每 100 g 精確 Decimal）驗證公式與邊界；
另以真實 nutrition_model 驗證追溯與生熟規則，以 PostgreSQL 驗證食譜載入。
"""
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.meal_calc.types import IngredientRequirement
from app.services import meal_calculation as mc
from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from recipe_workbook import build, ingredient, recipe

D = Decimal


class TargetCompositionTests(unittest.TestCase):
    def test_600_30_with_n_1_3_gives_final_target_780_39(self):
        targets = mc.compose_targets([mc.ParticipationBaseline("a:dining", D("600"), D("30"), D("1.3"))])
        self.assertEqual(targets.total_n, Fraction(13, 10))
        self.assertEqual(targets.energy_target, Fraction(780))
        self.assertEqual(targets.protein_target, Fraction(39))
        # 單人時基準參考值即原本的 600／30
        self.assertEqual(targets.energy_reference, Fraction(600))
        self.assertEqual(targets.protein_reference, Fraction(30))

    def test_members_with_different_baselines_and_bento_are_each_scaled_once(self):
        # A 晚餐用餐 600/30 n=1.15；A 另帶午餐便當 450/20 n=1.15；B 晚餐 500/25 n=0.7
        targets = mc.compose_targets([
            mc.ParticipationBaseline("A:dining", D("600"), D("30"), D("1.15")),
            mc.ParticipationBaseline("A:bento", D("450"), D("20"), D("1.15")),
            mc.ParticipationBaseline("B:dining", D("500"), D("25"), D("0.7")),
        ])
        # 人工：N=3.0；E=690+517.5+350=1557.5；P=34.5+23+17.5=75
        self.assertEqual(targets.total_n, Fraction(3))
        self.assertEqual(targets.energy_target, Fraction("1557.5"))
        self.assertEqual(targets.protein_target, Fraction(75))
        self.assertEqual(targets.protein_reference, Fraction(25))

    def test_individual_n_must_be_one_of_five_options_but_total_n_is_free(self):
        with self.assertRaises(ValueError):
            mc.compose_targets([mc.ParticipationBaseline("a", D("600"), D("30"), D("1.2"))])
        with self.assertRaises(ValueError):
            mc.compose_targets([
                mc.ParticipationBaseline("a", D("600"), D("30"), D("1")),
                mc.ParticipationBaseline("a", D("600"), D("30"), D("1")),
            ])
        # 0.7+0.85 = 1.55 不是五種選項之一，合計 N 不受限
        targets = mc.compose_targets([
            mc.ParticipationBaseline("a", D("600"), D("30"), D("0.7")),
            mc.ParticipationBaseline("b", D("600"), D("30"), D("0.85")),
        ])
        self.assertEqual(targets.total_n, Fraction("1.55"))

    def test_targets_from_t03_aggregate_match_its_participation_snapshots(self):
        from app.services.household import target_service

        member = {"member_id": "m1", "age_years": 30, "equation_sex": "F", "weight_kg": D("55"),
                  "activity_category": "low_active", "calculation_date": "2026-10-20", "goal": "general"}
        aggregate = target_service().aggregate_meal_targets([
            {"participation_id": "m1:dining", "kind": "dining", "member": member, "meal": "dinner", "n": D("1.3")},
            {"participation_id": "m1:bento", "kind": "bento", "member": member, "meal": "lunch", "n": D("1.3")},
        ])
        targets = mc.targets_from_t03(aggregate)
        self.assertEqual(targets.total_n, Fraction("2.6"))
        expected_e = sum(Fraction(D(p["scaled_target"]["energy_kcal"])) for p in aggregate["participations"])
        self.assertEqual(targets.energy_target, expected_e)
        ref = aggregate["reference_exact"]["protein_g"]
        self.assertEqual(targets.protein_reference, Fraction(int(ref["numerator"]), int(ref["denominator"])))


# 假 TFDA 表：每 100 g 的精確值；None 代表該營養素缺值；表中沒有代表查無食材
TABLE = {
    "雞胸肉": {"energy_kcal": D("100"), "protein_g": D("20"), "fat_g": D("2")},
    "白飯": {"energy_kcal": D("200"), "protein_g": D("0"), "fat_g": None},
    "糖": {"energy_kcal": D("400"), "protein_g": D("0"), "fat_g": D("0")},
    "蛋白": {"energy_kcal": D("0"), "protein_g": D("100"), "fat_g": D("0")},
}


class FakeCalculator:
    """依假表線性計算，並記錄每次呼叫的克數以確認有重算。"""

    def __init__(self, table=None):
        self.table = TABLE if table is None else table
        self.calls = []

    def __call__(self, rows):
        self.calls.append([(line.name, grams) for line, grams in rows])
        out = []
        for line, grams in rows:
            per100 = self.table.get(line.name)
            if per100 is None:
                out.append(mc.IngredientNutrition({}, {"sample_id": None}, "ingredient_not_found"))
                continue
            out.append(mc.IngredientNutrition(
                {k: (v * grams / 100 if v is not None else None) for k, v in per100.items()},
                {"sample_id": f"FAKE-{line.name}", "source_version": "fake-1"},
            ))
        return out


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name,
                             grams_per_n=None if grams is None else D(grams), **kw)


def single(n, energy="600", protein="30"):
    return mc.compose_targets([mc.ParticipationBaseline("a", D(energy), D(protein), D(n))])


# 雞胸肉 150 g = 150 kcal / 30 g；白飯 225 g = 450 kcal / 0 g → 基準 600／30
BASE_LINES = [line("雞胸肉", "150"), line("白飯", "225")]


class ScalingTests(unittest.TestCase):
    def test_baseline_600_30_scaled_by_1_3_is_judged_against_780_39(self):
        calc = FakeCalculator()
        result = mc.calculate_meal(BASE_LINES, single("1.3"), mode="ai", calculator=calc)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["baseline"]["energy_kcal"], D("600"))
        self.assertEqual(result["final"]["target"], {"energy_kcal": D("780"), "protein_g": D("39")})
        self.assertEqual(result["final"]["energy_kcal"], D("780"))
        self.assertEqual(result["final"]["protein_g"], D("39"))
        self.assertEqual(result["requirements"], [
            IngredientRequirement("F-雞胸肉", D("195")), IngredientRequirement("F-白飯", D("292.5")),
        ])
        # 整餐份量確實重新計算，而非沿用基準營養乘 N
        self.assertEqual(calc.calls[-1], [("雞胸肉", D("195")), ("白飯", D("292.5"))])

    def test_all_five_portion_options_hand_checked(self):
        # (n, 最終熱量, 最終蛋白質, 雞胸肉整餐克數, 白飯整餐克數)
        cases = [
            ("0.7", "420", "21", "105", "157.5"),
            ("0.85", "510", "25.5", "127.5", "191.25"),
            ("1", "600", "30", "150", "225"),
            ("1.15", "690", "34.5", "172.5", "258.75"),
            ("1.3", "780", "39", "195", "292.5"),
        ]
        for n, energy, protein, chicken, rice in cases:
            with self.subTest(n=n):
                result = mc.calculate_meal(BASE_LINES, single(n), mode="ai", calculator=FakeCalculator())
                self.assertTrue(result["adoptable"])
                self.assertEqual(result["final"]["target"], {"energy_kcal": D(energy), "protein_g": D(protein)})
                self.assertEqual(result["final"]["energy_kcal"], D(energy))
                self.assertEqual(result["final"]["protein_g"], D(protein))
                self.assertEqual([r.grams for r in result["requirements"]], [D(chicken), D(rice)])

    def test_display_grams_are_rounded_only_for_display(self):
        result = mc.calculate_meal(BASE_LINES, single("0.85"), mode="ai", calculator=FakeCalculator())
        rice = result["ingredients"][1]
        self.assertEqual(rice["whole_meal_grams"], D("191.25"))
        self.assertEqual(rice["display_grams"], D("191"))
        self.assertEqual(result["requirements"][1].grams, D("191.25"))

    def test_volume_units_are_displayed_to_half_a_spoon_and_unconverted_units_as_written(self):
        # P4（decisions #3）：每 1n 1.5 大匙 × 1.3 = 1.95 → 顯示 2 大匙；0.5 小匙 × 1.3 = 0.65 → 0.5 小匙
        lines = BASE_LINES + [
            line("醬油", None, unresolved_reason="unverified_conversion", unit="大匙", amount_per_n=D("1.5")),
            line("鹽", None, unresolved_reason="unverified_conversion", unit="小匙", amount_per_n=D("0.5")),
            line("雞蛋", None, unresolved_reason="unverified_conversion", unit="顆", amount_per_n=D("1")),
            line("胡椒", None, unresolved_reason="unresolved", raw_text="胡椒 少許"),
        ]
        rows = mc.calculate_meal(lines, single("1.3"), mode="ai", calculator=FakeCalculator())["ingredients"]
        shown = [(r["display_amount"], r["display_unit"], r["display_text"]) for r in rows]
        self.assertEqual(shown, [(D("195"), "g", None), (D("293"), "g", None), (D("2.0"), "大匙", None),
                                 (D("0.5"), "小匙", None), (D("1.3"), "顆", None), (None, None, "胡椒 少許")])
        self.assertEqual(mc.display_half(D("1.25")), D("1.5"))
        self.assertEqual(mc.display_half(D("1.24")), D("1.0"))

    def test_same_food_in_two_dishes_is_one_requirement(self):
        lines = [line("雞胸肉", "100", dish_id="d1"), line("白飯", "225", dish_id="d1"),
                 line("雞胸肉", "50", dish_id="d2")]
        result = mc.calculate_meal(lines, single("1.3"), mode="ai", calculator=FakeCalculator())
        self.assertEqual(result["requirements"], [
            IngredientRequirement("F-雞胸肉", D("195")), IngredientRequirement("F-白飯", D("292.5")),
        ])

    def test_traceability_is_kept_per_ingredient(self):
        result = mc.calculate_meal(BASE_LINES, single("1"), mode="ai", calculator=FakeCalculator())
        self.assertEqual(result["ingredients"][0]["trace"]["sample_id"], "FAKE-雞胸肉")


def sugar_protein(sugar, protein, **kw):
    # 糖 g×4 = 熱量；蛋白 g = 蛋白質
    return [line("糖", sugar, **kw), line("蛋白", protein)]


class BoundaryTests(unittest.TestCase):
    def check(self, sugar, protein):
        return mc.calculate_meal(sugar_protein(sugar, protein), single("1"), mode="ai", calculator=FakeCalculator())

    def test_90_and_110_percent_pass_inclusive_for_each_nutrient(self):
        for sugar, protein in (("135", "30"), ("165", "30"), ("150", "27"), ("150", "33"), ("135", "27")):
            with self.subTest(sugar=sugar, protein=protein):
                self.assertTrue(self.check(sugar, protein)["adoptable"])

    def test_just_outside_either_bound_is_not_adoptable_even_if_display_rounds_inside(self):
        # 134.99 g 糖 = 539.96 kcal，畫面取整為 540 仍不可通過
        for sugar, protein, nutrient in (("134.99", "30", "energy_kcal"), ("165.01", "30", "energy_kcal"),
                                         ("150", "26.99", "protein_g"), ("150", "33.01", "protein_g")):
            with self.subTest(sugar=sugar, protein=protein):
                result = self.check(sugar, protein)
                self.assertFalse(result["adoptable"])
                codes = [(r["code"], r.get("nutrient")) for r in result["block_reasons"]]
                self.assertIn(("NUTRITION_OUT_OF_RANGE", nutrient), codes)

    def test_non_terminating_reference_is_compared_exactly(self):
        # 三人各 1n、基準合計 1000 kcal → 參考值 333.33…；0.9 倍 = 300 整
        targets = mc.compose_targets([
            mc.ParticipationBaseline("a", D("400"), D("10"), D("1")),
            mc.ParticipationBaseline("b", D("300"), D("10"), D("1")),
            mc.ParticipationBaseline("c", D("300"), D("10"), D("1")),
        ])
        ok = mc.calculate_meal(sugar_protein("75", "10"), targets, mode="ai", calculator=FakeCalculator())
        self.assertTrue(ok["adoptable"])
        low = mc.calculate_meal(sugar_protein("74.999", "10"), targets, mode="ai", calculator=FakeCalculator())
        self.assertFalse(low["adoptable"])

    def test_final_is_recalculated_and_checked_against_scaled_target(self):
        class NonLinear(FakeCalculator):
            # 整餐量時多吸油：熱量多 20%，基準通過但整餐超出
            def __call__(self, rows):
                out = super().__call__(rows)
                if len(self.calls) > 1:
                    for item in out:
                        item.nutrients["energy_kcal"] *= D("1.2")
                return out

        result = mc.calculate_meal(BASE_LINES, single("1.3"), mode="ai", calculator=NonLinear())
        self.assertTrue(result["baseline"]["within_range"])
        self.assertFalse(result["adoptable"])
        self.assertEqual(result["final"]["energy_kcal"], D("936"))
        self.assertIn(("NUTRITION_OUT_OF_RANGE", "final"),
                      [(r["code"], r.get("stage")) for r in result["block_reasons"]])


class DatabaseModeAlignmentTests(unittest.TestCase):
    def test_adjusts_only_within_approved_range_then_scales(self):
        # 糖 120 g = 480 kcal（80%）不合格；已審核可調 100–200 g → 對齊後再乘 N
        lines = sugar_protein("120", "30", adjustable_min=D("100"), adjustable_max=D("200"),
                              adjustment_approved=True)
        result = mc.calculate_meal(lines, single("1.3"), mode="database", calculator=FakeCalculator())
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertTrue(result["baseline"]["adjusted"])
        sugar = result["ingredients"][0]["baseline_grams"]
        self.assertTrue(D("135") <= sugar <= D("165"))
        self.assertTrue(D("100") <= sugar <= D("200"))
        self.assertEqual(result["requirements"][0].grams, sugar * D("1.3"))
        self.assertEqual(result["final"]["target"], {"energy_kcal": D("780"), "protein_g": D("39")})

    def test_unapproved_range_is_not_used(self):
        lines = sugar_protein("120", "30", adjustable_min=D("100"), adjustable_max=D("200"),
                              adjustment_approved=False)
        result = mc.calculate_meal(lines, single("1"), mode="database", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        self.assertFalse(result["baseline"]["adjusted"])
        self.assertIn("NO_FEASIBLE_BASELINE", [r["code"] for r in result["block_reasons"]])

    def test_ai_mode_never_adjusts_candidate_quantities(self):
        lines = sugar_protein("120", "30", adjustable_min=D("100"), adjustable_max=D("200"),
                              adjustment_approved=True)
        result = mc.calculate_meal(lines, single("1"), mode="ai", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        self.assertEqual(result["ingredients"][0]["baseline_grams"], D("120"))

    def test_no_feasible_solution_is_reported_without_moving_target_or_tolerance(self):
        # 可調上限 130 g = 520 kcal，仍低於 540：無解
        lines = sugar_protein("120", "30", adjustable_min=D("100"), adjustable_max=D("130"),
                              adjustment_approved=True)
        result = mc.calculate_meal(lines, single("1.3"), mode="database", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        codes = [r["code"] for r in result["block_reasons"]]
        self.assertIn("NO_FEASIBLE_BASELINE", codes)
        self.assertEqual(result["baseline"]["reference"], {"energy_kcal": D("600"), "protein_g": D("30")})
        self.assertEqual(result["final"]["target"], {"energy_kcal": D("780"), "protein_g": D("39")})
        out = [r for r in result["block_reasons"] if r["code"] == "NUTRITION_OUT_OF_RANGE"][0]
        self.assertEqual((out["lower"], out["upper"]), (D("540"), D("660")))
        # P2：仍可查看——每項整餐用量（標示未驗證）、可計算的成本需求量與具體調整建議
        self.assertFalse(result["quantities_verified"])
        self.assertEqual(result["requirements"], [IngredientRequirement("F-糖", D("156")),
                                                  IngredientRequirement("F-蛋白", D("39"))])
        self.assertEqual(result["ingredients"][0]["whole_meal_grams"], D("156"))
        self.assertEqual(result["final"]["energy_kcal"], D("624"))
        [hint] = result["suggestions"]
        self.assertEqual((hint["code"], hint["nutrient"], hint["direction"], hint["needed_change"]),
                         ("NUTRITION_OUT_OF_RANGE", "energy_kcal", "increase", D("60")))
        self.assertFalse(hint["within_adjustable_ranges"])
        [sugar] = hint["ingredients"]
        self.assertEqual((sugar["name"], sugar["direction"], sugar["adjustable_min"], sugar["adjustable_max"],
                          sugar["max_change"], sugar["adjustment_approved"]),
                         ("糖", "increase", D("100"), D("130"), D("40"), True))

    def test_suggestion_says_when_the_approved_range_would_be_enough_for_one_nutrient(self):
        # 糖可調 100–200 g，但蛋白質 20 g 不足且蛋白不可調：熱量可補、蛋白質無法在範圍內補足
        lines = sugar_protein("120", "20", adjustable_min=D("100"), adjustable_max=D("200"),
                              adjustment_approved=True)
        result = mc.calculate_meal(lines, single("1"), mode="database", calculator=FakeCalculator())
        hints = {h["nutrient"]: h for h in result["suggestions"]}
        self.assertTrue(hints["energy_kcal"]["within_adjustable_ranges"])
        protein = hints["protein_g"]
        self.assertEqual((protein["direction"], protein["needed_change"]), ("increase", D("7")))
        self.assertFalse(protein["within_adjustable_ranges"])
        [egg_white] = protein["ingredients"]
        self.assertEqual((egg_white["name"], egg_white["adjustment_approved"], egg_white["max_change"]),
                         ("蛋白", False, None))

    def test_both_energy_and_protein_must_fit_at_once(self):
        # 只有糖可調：熱量可修正，但蛋白質 20 g（67%）仍不合格 → 無解
        lines = sugar_protein("120", "20", adjustable_min=D("100"), adjustable_max=D("200"),
                              adjustment_approved=True)
        result = mc.calculate_meal(lines, single("1"), mode="database", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        self.assertIn(("NUTRITION_OUT_OF_RANGE", "protein_g"),
                      [(r["code"], r.get("nutrient")) for r in result["block_reasons"]])


class CompletenessTests(unittest.TestCase):
    def test_unresolved_weight_blocks_adoption_and_is_not_zero(self):
        lines = BASE_LINES + [line("醬油", None, unresolved_reason="unverified_conversion")]
        result = mc.calculate_meal(lines, single("1"), mode="database", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        codes = [r["code"] for r in result["block_reasons"]]
        self.assertIn("INGREDIENT_UNRESOLVED", codes)
        self.assertIn("NUTRITION_INCOMPLETE", codes)
        self.assertIsNone(result["baseline"]["energy_kcal"])
        self.assertEqual(result["unresolved_ingredients"][0]["reason"], "unverified_conversion")
        # P2：已知克數的食材仍列出整餐用量與成本需求量；無法換算者不填 0
        self.assertEqual([r.food_id for r in result["requirements"]], ["F-雞胸肉", "F-白飯"])
        self.assertEqual(result["ingredients"][0]["whole_meal_grams"], D("150"))
        self.assertIsNone(result["ingredients"][2]["whole_meal_grams"])
        [hint] = result["suggestions"]
        self.assertEqual((hint["code"], [i["name"] for i in hint["ingredients"]]), ("INGREDIENT_UNRESOLVED", ["醬油"]))

    def test_missing_required_nutrient_blocks_until_filled_and_revalidated(self):
        lines = BASE_LINES + [line("沙拉油", "0.5")]  # 假表中沒有沙拉油
        result = mc.calculate_meal(lines, single("1"), mode="ai", calculator=FakeCalculator())
        self.assertFalse(result["adoptable"])
        self.assertIsNone(result["baseline"]["energy_kcal"])
        block = [r for r in result["block_reasons"] if r["code"] == "NUTRITION_INCOMPLETE"][0]
        self.assertEqual(block["ingredients"]["energy_kcal"], ["沙拉油"])
        self.assertEqual(len(result["requirements"]), 3)  # P2：成本仍可計算
        self.assertFalse(result["quantities_verified"])
        [hint] = result["suggestions"]
        self.assertEqual((hint["code"], [i["name"] for i in hint["ingredients"]]), ("NUTRITION_INCOMPLETE", ["沙拉油"]))
        # 補齊 TFDA 資料後重新驗證即可採用（沙拉油 0.5 g = 4 kcal，仍在範圍內）
        filled = dict(TABLE, 沙拉油={"energy_kcal": D("800"), "protein_g": D("0"), "fat_g": D("100")})
        again = mc.calculate_meal(lines, single("1"), mode="ai", calculator=FakeCalculator(filled))
        self.assertTrue(again["adoptable"], again["block_reasons"])
        self.assertEqual(again["baseline"]["energy_kcal"], D("604"))

    def test_missing_display_only_nutrient_is_shown_as_none_and_does_not_block(self):
        result = mc.calculate_meal(BASE_LINES, single("1"), mode="ai", calculator=FakeCalculator())
        self.assertTrue(result["adoptable"])
        self.assertIsNone(result["final"]["other_nutrients"]["fat_g"])  # 白飯脂肪缺值
        self.assertEqual(result["final"]["missing_nutrients"]["fat_g"], ["白飯"])


class TfdaCalculatorTests(unittest.TestCase):
    """使用真實 T04 nutrition_model（TFDA 資料），不呼叫 LLM。"""

    def calc(self, *lines):
        return mc.tfda_calculator([(ln, ln.grams_per_n) for ln in lines])

    def test_cooked_tfda_record_with_cooked_weight_is_traceable_and_not_corrected_again(self):
        # 白飯 A0550601 為熟食樣品：183 kcal、3.1 g 蛋白質／100 g；200 g 熟重直接換算，不再套烹調係數
        [rice] = self.calc(line("白飯", "200", weight_basis="cooked", cooking_method="蒸煮"))
        self.assertIsNone(rice.error)
        self.assertEqual(rice.nutrients["energy_kcal"], D("366"))
        self.assertEqual(rice.nutrients["protein_g"], D("6.2"))
        self.assertIsNone(rice.nutrients["sugars_g"])  # TFDA 缺值保持 None
        self.assertEqual(rice.trace["sample_id"], "A0550601")
        self.assertEqual(rice.trace["nutrition_basis"], "cooked_tfda")
        self.assertIn("TFDA", rice.trace["source_version"])
        self.assertFalse(rice.trace["is_estimated"])

    def test_cooked_record_given_as_raw_weight_is_rejected(self):
        [rice] = self.calc(line("白飯", "200", weight_basis="raw"))
        self.assertEqual(rice.error, "cooked_food_requires_cooked_weight_basis")
        self.assertIsNone(rice.nutrients["energy_kcal"])

    def test_raw_food_without_approved_cooking_basis_is_incomplete_not_zero(self):
        [chicken] = self.calc(line("雞胸肉", "150", cooking_method="烤"))
        self.assertIsNone(chicken.nutrients["energy_kcal"])
        self.assertIsNone(chicken.nutrients["protein_g"])
        self.assertEqual(chicken.trace["sample_id"], "I0402402")  # 仍追溯到生食樣品
        result = mc.calculate_meal([line("雞胸肉", "150", cooking_method="烤")], single("1"),
                                   mode="ai", calculator=mc.tfda_calculator)
        self.assertFalse(result["adoptable"])
        self.assertIn("NUTRITION_INCOMPLETE", [r["code"] for r in result["block_reasons"]])

    def test_oil_counted_as_used(self):
        [oil] = self.calc(line("大豆油", "5", consumed_as_is=True))
        self.assertEqual(oil.nutrients["energy_kcal"], D("44.2"))
        self.assertEqual(oil.trace["sample_id"], "M1100101")

    def test_grams_are_multiplied_exactly_with_decimal_not_float(self):
        # P5：白飯 183 kcal／3.1 g 每 100 g；33.3 g → 60.939 kcal、1.0323 g（float 會得到 60.93899999999999）
        [rice] = self.calc(line("白飯", "33.3", weight_basis="cooked"))
        self.assertEqual(rice.nutrients["energy_kcal"], D("60.939"))
        self.assertEqual(rice.nutrients["protein_g"], D("1.0323"))
        self.assertEqual(rice.trace["cooked_weight_g"], D("33.3"))

    def test_trace_records_the_weight_basis_source(self):
        [egg] = self.calc(line("大豆油", "5", consumed_as_is=True, weight_basis_source=mc.WORKBOOK_RAW_CONVENTION))
        self.assertEqual((egg.trace["weight_basis"], egg.trace["weight_basis_source"]),
                         ("raw", mc.WORKBOOK_RAW_CONVENTION))

    def test_exact_90_and_110_percent_boundaries_pass_with_real_tfda_values(self):
        # AC12：熱量正好 90%（下界）、47.1 g 時熱量與蛋白質正好 110%（上界），都必須可採用
        lower = mc.MealTargets(Fraction(1), Fraction(60939, 900), Fraction(10323, 10000))
        result = mc.calculate_meal([line("白飯", "33.3", weight_basis="cooked")], lower, mode="ai",
                                   calculator=mc.tfda_calculator)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["baseline"]["energy_kcal"], D("60.939"))
        upper = mc.MealTargets(Fraction(1), Fraction(86193, 1100), Fraction(14601, 11000))
        result = mc.calculate_meal([line("白飯", "47.1", weight_basis="cooked")], upper, mode="ai",
                                   calculator=mc.tfda_calculator)
        self.assertTrue(result["adoptable"], result["block_reasons"])

    def test_unknown_food_is_unresolved(self):
        [x] = self.calc(line("不存在的食材", "10", consumed_as_is=True))
        self.assertEqual(x.error, "ingredient_not_found")
        self.assertIsNone(x.nutrients["energy_kcal"])


@requires_db
class RecipeLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def run_import(self, recipes):
        from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
        bundle = parse_recipe_workbook(build(Path(self.folder.name) / "book.xlsx", recipes))
        with session_factory()() as session, session.begin():
            import_recipe_bundle(session, bundle)

    def load(self, recipe_id):
        with session_factory()() as session:
            return mc.load_recipe_lines(session, recipe_id)

    def test_lines_are_per_1n_with_reviewed_ranges_and_unresolved_units(self):
        self.run_import([recipe("R001", "蛋炒飯", servings="2人份", ingredients=[
            ingredient("FOOD000100", "白飯", 400, adjustable_min=300, adjustable_max=500),
            ingredient("FOOD000101", "大豆油", 10, adjustment_status="pending"),
            ingredient("FOOD001541", "醬油", 1, raw_text="醬油 1匙", unit="匙", weight_g=None),
        ])])
        rice, oil, soy = self.load("R001")
        self.assertEqual((rice.food_id, rice.name, rice.grams_per_n), ("FOOD000100", "白飯", D("200")))
        self.assertEqual((rice.adjustable_min, rice.adjustable_max), (D("150"), D("250")))
        self.assertTrue(rice.adjustment_approved)
        self.assertEqual(rice.cooking_method, "炒")
        self.assertEqual(rice.dish_id, "R001")
        # P6／decisions #17：工作簿克數一律視為生重，依據可追溯
        self.assertEqual((rice.weight_basis, rice.weight_basis_source), ("raw", mc.WORKBOOK_RAW_CONVENTION))
        self.assertEqual(oil.grams_per_n, D("5"))
        self.assertFalse(oil.adjustment_approved)
        self.assertIsNone(soy.grams_per_n)
        self.assertIsNotNone(soy.unresolved_reason)
        result = mc.calculate_meal([rice, oil, soy], single("1"), mode="database", calculator=FakeCalculator())
        self.assertIn("INGREDIENT_UNRESOLVED", [r["code"] for r in result["block_reasons"]])

    def test_unknown_original_servings_makes_baseline_not_computable(self):
        self.run_import([recipe("R002", "清炒高麗菜", servings="適量")])
        [cabbage] = self.load("R002")
        self.assertIsNone(cabbage.grams_per_n)
        self.assertEqual(cabbage.unresolved_reason, "servings_unresolved")


if __name__ == "__main__":
    unittest.main()
