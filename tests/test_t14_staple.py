"""P1（decisions #16）：主食對應一個 TFDA 食材，預設每人份量與可調範圍；計入營養與 ±10%、
資料庫模式與菜色一起對齊，但不計入價格／缺料。主食含過敏／禁吃食材時回報、不替換。

沿用 T14 整條流程；營養以每克固定含量的假 calculator 計算（主食與菜色同一含量），
合成食譜每道兩項食材、每項 50 g/n。
"""
import json
import unittest
from decimal import Decimal

from sqlalchemy import text

import db_support
from test_t14_generation import FakeResponder, GenerationTestCase, ai_dish, menu, CABBAGE, EGG, GARLIC, TOMATO

RICE = "FOOD000296"


class StapleTests(GenerationTestCase):
    def generate(self, *, staple="白飯", grams_for_reference=Decimal(350), responder=None, **fields):
        self.create_request(staple=staple, dish_count=2, **fields)
        job_id = self.submit()
        self.run_job(responder, calculator=self.calculator_for(grams_for_reference))
        return self.job(job_id)

    def payload(self):
        [row] = self.results()
        return row.payload

    def test_staple_counts_for_nutrition_but_not_for_price(self):
        # 菜色 200 g/n ＋ 白飯 150 g/n = 350 g/n → 恰為參考值
        job = self.generate()
        self.assertEqual(job["status"], "succeeded", job)
        payload = self.payload()
        calc = payload["calculation"]
        self.assertTrue(calc["adoptable"], calc["block_reasons"])
        [rice] = [r for r in calc["ingredients"] if r["role"] == "staple"]
        self.assertEqual((rice["food_id"], rice["name"], Decimal(rice["baseline_grams"])), (RICE, "白飯", Decimal(150)))
        self.assertEqual(rice["weight_basis"], "cooked")
        self.assertNotIn(RICE, [r["food_id"] for r in calc["requirements"]])
        self.assertNotIn(RICE, [ln["food_id"] for ln in payload["cost"]["lines"]])
        self.assertEqual((payload["staple"]["label"], payload["staple"]["review_status"]), ("白飯", "pending_review"))
        view = self.client.get(f"/meal-requests/{self.request['id']}/results/1").json()
        self.assertEqual((view["staple"]["label"], Decimal(view["staple"]["display_amount"])), ("白飯", Decimal(150)))
        self.assertNotIn(RICE, [i["food_id"] for i in view["prices"]["items"]])

    def test_staple_is_aligned_together_with_dishes_within_its_range(self):
        # 參考值對應 300 g/n：200＋150＝350 g 超出 110%；白飯可調 100–250 g → 調到 ≤130 g 即可通過
        job = self.generate(grams_for_reference=Decimal(300))
        self.assertEqual(job["status"], "succeeded", job)
        calc = self.payload()["calculation"]
        self.assertTrue(calc["adoptable"], calc["block_reasons"])
        self.assertTrue(calc["baseline"]["adjusted"])
        [rice] = [r for r in calc["ingredients"] if r["role"] == "staple"]
        self.assertTrue(Decimal(100) <= Decimal(rice["baseline_grams"]) <= Decimal(130))

    def test_no_staple_means_no_staple_line(self):
        job = self.generate(staple="不需要主食", grams_for_reference=Decimal(200))
        self.assertEqual(job["status"], "succeeded", job)
        payload = self.payload()
        self.assertIsNone(payload["staple"])
        self.assertEqual([r for r in payload["calculation"]["ingredients"] if r["role"] == "staple"], [])

    def test_unsupported_staple_is_unresolved_not_zero(self):
        job = self.generate(staple="披薩")
        self.assertEqual(job["status"], "succeeded", job)
        calc = self.payload()["calculation"]
        self.assertFalse(calc["adoptable"])
        self.assertIn("INGREDIENT_UNRESOLVED", [b["code"] for b in calc["block_reasons"]])
        [rice] = [r for r in calc["ingredients"] if r["role"] == "staple"]
        self.assertIsNone(rice["whole_meal_grams"])

    def test_ai_prompt_gets_the_staple_and_the_remaining_reference(self):
        self.clear_recipes()
        responder = FakeResponder(menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], method="煮"),
                                       ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)])))
        job = self.generate(responder=responder)
        self.assertEqual(job["status"], "succeeded", job)
        data = json.loads(responder.calls[0]["input_text"])
        self.assertEqual((data["staple"]["label"], Decimal(data["staple"]["grams_per_n"])), ("白飯", Decimal(150)))
        energy, _ = self.reference()
        # 主食已佔 150/350 → 菜色每份只需 200/350 的參考熱量
        self.assertEqual(Decimal(data["per_n_reference"]["energy_kcal"]).quantize(Decimal("0.01")),
                         (energy * 200 / 350).quantize(Decimal("0.01")))
        self.assertTrue(self.payload()["calculation"]["adoptable"])


class StapleAllergyTests(GenerationTestCase):
    def allergens(self):
        # 在建立成員前把白飯放進 FOOD 字典（成員的過敏食材必須是字典內的 FOOD 編號）
        with db_support._engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO ingredient_aliases (food_id, canonical_name, aliases, ambiguous_aliases, review_status, "
                "quality_issues) VALUES (:id, '白飯', '[]', '[]', 'pending', '[]') ON CONFLICT DO NOTHING"),
                {"id": RICE})
        return [RICE]

    def test_staple_with_allergen_is_reported_not_swapped(self):
        self.create_request(staple="白飯", dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "STAPLE_DIETARY_CONFLICT")
        self.assertIn("白飯", job["error"]["message"])
        self.assertEqual(responder.calls, [])
        self.assertEqual(self.results(), [])


if __name__ == "__main__":
    unittest.main()
