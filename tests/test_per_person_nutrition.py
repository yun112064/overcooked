"""每人營養對照每日建議（結果頁畫面用；不參與驗證與採用資格）。"""
import unittest
from decimal import Decimal

from app.services.meal_results import _dishes
from app.services.per_person_nutrition import daily_reference, per_person


def payload(items, total_n="2"):
    return {"targets": {"total_n": total_n, "items": items}}


def item(member, kind, n, ratio, energy, protein, meal="dinner"):
    # scaled_target = 每日 × 餐別比例 × n（T03 aggregate_meal_targets）
    return {"participation_id": f"{member}:{kind}", "kind": kind, "meal": meal, "meal_ratio": ratio, "n": n,
            "scaled_target": {"energy_kcal": str(Decimal(energy) * Decimal(ratio) * Decimal(n)),
                              "protein_g": str(Decimal(protein) * Decimal(ratio) * Decimal(n))}}


NUTRITION = {"total_n": "2", "energy_kcal": "1600", "protein_g": "60",
             "other_nutrients": {"carbohydrate_g": "200", "fat_g": "50", "sugars_g": "20", "sodium_mg": None,
                                 "dietary_fiber_g": "10"},
             "known_partial": {"sodium_mg": "900"}}


class DailyReferenceTests(unittest.TestCase):
    def test_other_nutrients_follow_daily_energy(self):
        ref = daily_reference(Decimal(2000), Decimal(60))
        self.assertEqual((ref["energy_kcal"]["kind"], Decimal(ref["energy_kcal"]["value"])), ("target", 2000))
        self.assertEqual(Decimal(ref["protein_g"]["value"]), 60)
        # 碳水 50–60% ÷ 4、脂肪 20–30% ÷ 9、糖 <10% ÷ 4、鈉 <2400、纖維 14 g / 1000 kcal
        self.assertEqual((Decimal(ref["carbohydrate_g"]["min"]), Decimal(ref["carbohydrate_g"]["max"])), (250, 300))
        self.assertAlmostEqual(float(ref["fat_g"]["min"]), 2000 * 0.2 / 9)
        self.assertEqual((ref["sugars_g"]["kind"], Decimal(ref["sugars_g"]["max"])), ("max", 50))
        self.assertIn("caveat", ref["sugars_g"])
        self.assertEqual((ref["sodium_mg"]["kind"], Decimal(ref["sodium_mg"]["max"])), ("max", 2400))
        self.assertEqual(Decimal(ref["dietary_fiber_g"]["value"]), 28)


class PerPersonTests(unittest.TestCase):
    def test_meal_is_split_by_each_members_meal_target_not_by_portion(self):
        # 當餐目標：a＝2500×0.4×1.3＝1300 kcal，b＝1500×0.4×0.7＝420 kcal → a 分到 1300/1720
        rows = per_person(payload([item("a", "dining", "1.3", "0.4", 2500, 80), item("b", "dining", "0.7", "0.4", 1500, 50)]),
                          NUTRITION, {"a": "爸爸"})
        a, b = rows
        self.assertEqual((a["name"], b["name"]), ("爸爸", None))  # 成員已刪除時名稱為 None
        self.assertAlmostEqual(float(a["intake"]["energy_kcal"]), 1600 * 1300 / 1720)
        self.assertAlmostEqual(float(b["intake"]["protein_g"]), 60 * 420 / 1720)
        self.assertEqual(Decimal(a["daily"]["energy_kcal"]["value"]), 2500)
        self.assertEqual(Decimal(b["daily"]["protein_g"]["value"]), 50)
        # 這一餐應占每日的比例＝餐別比例 × n
        self.assertEqual((Decimal(a["meal_ratio"]), Decimal(b["meal_ratio"])), (Decimal("0.52"), Decimal("0.28")))

    def test_child_with_low_need_is_not_overfed_by_portion(self):
        # 整餐剛好等於目標合計時，每個人都剛好吃到自己的當餐目標（不因 n 相同而平分）
        rows = per_person(payload([item("adult", "dining", "1", "0.4", 2400, 70), item("kid", "dining", "1", "0.4", 1200, 40)]),
                          {**NUTRITION, "energy_kcal": "1440"}, {})
        adult, kid = rows
        self.assertAlmostEqual(float(adult["intake"]["energy_kcal"]), 960)  # 2400 × 40%
        self.assertAlmostEqual(float(kid["intake"]["energy_kcal"]), 480)    # 1200 × 40%

    def test_unknown_nutrient_stays_unknown_with_known_part_scaled(self):
        [a, _] = per_person(payload([item("a", "dining", "1", "0.4", 2000, 60), item("b", "dining", "1", "0.4", 2000, 60)]),
                            NUTRITION, {})
        self.assertIsNone(a["intake"]["sodium_mg"])
        self.assertEqual(Decimal(a["known_partial"]["sodium_mg"]), 450)

    def test_bento_is_listed_separately_not_added_to_this_meal(self):
        # 2026-10-28：帶便當不讓當餐營養變成兩倍；整餐依當餐目標分給「媽媽用餐」「媽媽便當」「b 用餐」
        rows = per_person(payload([item("a", "dining", "1", "0.4", 2000, 60),
                                   item("a", "bento", "1", "0.3", 2000, 60, meal="lunch"),
                                   item("b", "dining", "1", "0.4", 2000, 60)]), NUTRITION, {"a": "媽媽"})
        a, b = rows
        self.assertEqual((a["kinds"], a["dining"]), (["dining", "bento"], True))
        # 目標：用餐 800、便當 600、b 800 → 合計 2200
        self.assertEqual((Decimal(a["n"]), Decimal(a["meal_ratio"])), (1, Decimal("0.4")))
        self.assertAlmostEqual(float(a["intake"]["energy_kcal"]), 1600 * 800 / 2200)
        self.assertEqual(a["bento"]["meal"], "lunch")
        self.assertEqual(Decimal(a["bento"]["meal_ratio"]), Decimal("0.3"))
        self.assertAlmostEqual(float(a["bento"]["intake"]["energy_kcal"]), 1600 * 600 / 2200)
        self.assertAlmostEqual(float(a["bento"]["known_partial"]["sodium_mg"]), 900 * 600 / 2200)
        self.assertIsNone(b["bento"])
        self.assertAlmostEqual(float(a["intake"]["energy_kcal"]) + float(a["bento"]["intake"]["energy_kcal"])
                               + float(b["intake"]["energy_kcal"]), 1600)  # 整餐全部分完
        self.assertEqual(Decimal(a["daily"]["energy_kcal"]["value"]), 2000)

    def test_bento_only_member_has_no_intake_for_this_meal(self):
        [a, _] = per_person(payload([item("a", "bento", "1", "0.3", 2000, 60, meal="lunch"),
                                     item("b", "dining", "1", "0.4", 2000, 60)]), NUTRITION, {})
        self.assertFalse(a["dining"])
        self.assertEqual(Decimal(a["meal_ratio"]), 0)
        self.assertIsNone(a["intake"]["energy_kcal"])
        self.assertAlmostEqual(float(a["bento"]["intake"]["energy_kcal"]), 1600 * 600 / 1400)
        self.assertEqual(Decimal(a["daily"]["energy_kcal"]["value"]), 2000)

    def test_missing_targets_return_no_rows(self):
        self.assertEqual(per_person({}, NUTRITION, {}), [])


class HighSodiumDishTests(unittest.TestCase):
    """每道菜每人份鈉 ≥ 1000 mg 標示高鈉（只提醒）；任一食材缺鈉值則未知、不標示。"""

    @staticmethod
    def result(rows, total_n="2"):
        dishes = [{"standard_dish_id": f"D{i}", "dish_name": f"菜{i}", "source": "database", "recipe_id": f"R{i}"}
                  for i in (1, 2, 3)]
        return {"dishes": dishes, "calculation": {"total_n": total_n, "ingredients": rows}}

    @staticmethod
    def row(dish, sodium):
        return {"dish_id": dish, "food_id": "F", "name": "x", "nutrients": {"sodium_mg": sodium}}

    def test_sodium_is_per_serving_and_flags_from_the_threshold(self):
        rows = [self.row("R1", "1500"), self.row("R1", "500"),  # 2000 ÷ 2 份 ＝ 1000 → 高鈉（含等於）
                self.row("R2", "1998"),                          # 999 → 不標示
                self.row("R3", "800"), self.row("R3", None)]     # 有食材缺值 → 未知
        d1, d2, d3 = _dishes(self.result(rows))
        self.assertEqual((Decimal(d1["sodium_mg_per_serving"]), d1["high_sodium"]), (Decimal(1000), True))
        self.assertEqual((Decimal(d2["sodium_mg_per_serving"]), d2["high_sodium"]), (Decimal(999), False))
        self.assertEqual((d3["sodium_mg_per_serving"], d3["high_sodium"]), (None, False))


if __name__ == "__main__":
    unittest.main()
