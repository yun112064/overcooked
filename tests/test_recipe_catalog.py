"""食譜庫選菜（2026-11-04）：分類、菜名可被 T11 唯一對到、標出與本餐禁忌衝突的菜。"""
import unittest

from test_t11_recipe_retrieval import CARROT, EGG, seed_recipes
from test_t14_generation import GenerationTestCase


class StapleKindTests(unittest.TestCase):
    def test_staple_dishes_are_split_into_rice_noodles_porridge_and_others(self):
        from app.services.recipe_catalog import staple_kind
        cases = {"滷肉飯": "飯", "牛肉丼飯": "飯", "咖哩烏龍麵": "麵", "台式炒米粉": "麵", "皮蛋瘦肉粥": "粥",
                 "培根蛋餅": "其他主食"}
        self.assertEqual({n: staple_kind(n, "主食") for n in cases}, cases)
        self.assertIsNone(staple_kind("番茄炒飯風味蛋", "主菜"))


class MainIngredientTests(unittest.TestCase):
    def test_meats_seafood_egg_tofu_and_vegetable_dishes(self):
        from app.services.recipe_catalog import main_ingredients
        cases = [
            (["雞腿肉", "醬油"], ["雞肉"]),
            (["五花肉片", "高麗菜", "雞蛋"], ["蛋", "豬肉"]),
            (["牛肋條", "白蘿蔔"], ["牛肉"]),
            (["火鍋羊肉片", "青蔥"], ["羊肉"]),
            (["蝦仁", "透抽"], ["海鮮"]),
            # 「雞蛋豆腐」是豆腐、牛蒡是蔬菜；沒有肉或海鮮的菜歸蔬菜
            (["雞蛋豆腐", "牛蒡"], ["豆腐", "蔬菜（無肉）"]),
            # 調味與提味（蠔油、雞粉、柴魚、蝦米）不讓菜變成海鮮或雞肉
            (["高麗菜", "蠔油", "雞粉", "柴魚", "蝦米"], ["蔬菜（無肉）"]),
        ]
        for names, expected in cases:
            self.assertEqual(main_ingredients(names), expected, names)


class RecipeCatalogApiTests(GenerationTestCase):
    def allergens(self):
        return [CARROT]

    def catalog(self):
        res = self.client.get(f"/meal-requests/{self.request['id']}/recipe-catalog")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_dishes_are_grouped_once_with_facets_and_conflicts(self):
        seed_recipes(self.factory, [("R20", "蛋炒飯", [], [EGG], ["炒"], True, "主食")])
        self.create_request()
        data = self.catalog()
        names = [d["dish_name"] for d in data["dishes"]]
        # 番茄炒蛋兩個來源只列一次；未通過審核的不列；主菜排在主食前面
        self.assertEqual(names.count("番茄炒蛋"), 1)
        self.assertNotIn("九層塔炒番茄", names)
        self.assertEqual(names[-1], "蛋炒飯")
        by_name = {d["dish_name"]: d for d in data["dishes"]}
        self.assertEqual(by_name["蛋炒飯"]["staple_kind"], "飯")
        self.assertEqual(by_name["紅蘿蔔炒蛋"]["conflicts"], [CARROT])
        self.assertEqual(by_name["番茄炒蛋"]["conflicts"], [])
        self.assertEqual(data["facets"]["category"], [{"value": "主菜", "count": 6}, {"value": "主食", "count": 1}])
        self.assertEqual(data["facets"]["staple_kind"], [{"value": "飯", "count": 1}])
        # 合成食譜的食材名稱是 FOOD 編號（分類規則另由 MainIngredientTests 測）：都歸蔬菜
        self.assertEqual(data["facets"]["main_ingredient"], [{"value": "蔬菜（無肉）", "count": 7}])

    def test_name_shared_by_two_dishes_is_left_out(self):
        # 「炒青菜」是兩道菜的別名：若有一道菜就叫炒青菜，T11 會判為對到多道菜，不能列出讓人勾
        seed_recipes(self.factory, [("R21", "炒青菜", [], [EGG], ["炒"], True)])
        self.create_request()
        self.assertNotIn("炒青菜", [d["dish_name"] for d in self.catalog()["dishes"]])

    def test_other_households_request_is_not_found(self):
        self.create_request()
        other = self.client.__class__(self.client.app)
        self.assertNotEqual(other.get(f"/meal-requests/{self.request['id']}/recipe-catalog").status_code, 200)

    def test_picker_request_needs_dishes_but_not_budget(self):
        self.create_request(mode="picker", budget=None, specified_dishes=[])
        self.assertEqual([i["reason"] for i in self.request["issues"]], ["REQUIRED"])
        self.assertEqual(self.request["issues"][0]["field"], "specified_dishes")
        self.create_request(mode="picker", budget=None, specified_dishes=["番茄炒蛋"])
        self.assertTrue(self.request["complete"], self.request["issues"])
        # 一般問卷仍要填預算
        self.create_request(budget=None)
        self.assertIn("budget", [i["field"] for i in self.request["issues"]])


class EstimatedAmountDisplayTests(unittest.TestCase):
    def test_reference_amount_shows_grams_instead_of_made_up_count(self):
        from decimal import Decimal

        from app.services.meal_calculation import IngredientLine, _display
        line = IngredientLine(food_id="F1", name="袖珍菇", grams_per_n=Decimal("50"), unit="條",
                              amount_per_n=Decimal("12.5"), raw_text="袖珍菇 數條", amount_estimated=True)
        self.assertEqual(_display(line, Decimal("157.4"), Decimal("3.15"))["display_unit"], "g")
        exact = IngredientLine(food_id="F2", name="雞蛋", grams_per_n=Decimal("50"), unit="顆", amount_per_n=Decimal("1"))
        self.assertEqual(_display(exact, Decimal("157.5"), Decimal("3.15"))["display_unit"], "顆")


if __name__ == "__main__":
    unittest.main()
