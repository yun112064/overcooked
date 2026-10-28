"""預算下限（2026-10-28 使用者決定：預算低於估算的最低合理金額時不能送出生成）。"""
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from app.services.budget_floor import MIN_PRICED_DISHES, RecipeFloor, reference_dish_cost
from db_support import requires_db

D = Decimal


def dish(i, cost, category="主菜", food=None, known=True):
    """每 1n 用 100 克、單位價 cost/100 的一道菜。"""
    return RecipeFloor(f"R{i}", f"DISH{i}", category, ((food or f"F{i}", D(100)),), known), (food or f"F{i}", D(cost) / 100)


def build(entries):
    floors, prices = [], {}
    for floor, (food, price) in entries:
        floors.append(floor)
        prices[food] = price
    return floors, prices


class ReferenceDishCostTests(unittest.TestCase):
    def test_uses_a_low_percentile_not_the_minimum(self):
        # 一道資料錯誤的 0.1 元菜不會把下限拉到幾毛錢
        floors, prices = build([dish(0, "0.1")] + [dish(i, 10 + i) for i in range(1, 40)])
        self.assertEqual(reference_dish_cost(floors, prices, set(), has_staple=True), D(11))

    def test_unpriced_desserts_and_staples_are_not_counted(self):
        entries = [dish(i, 20 + i) for i in range(MIN_PRICED_DISHES)]
        entries += [dish(100 + i, 1, category="點心") for i in range(30)]
        entries += [dish(200 + i, 2, category="主食") for i in range(30)]
        floors, prices = build(entries)
        floors += [RecipeFloor(f"X{i}", f"DISHX{i}", "主菜", ((f"NOPRICE{i}", D(100)),)) for i in range(30)]
        floors += [RecipeFloor(f"U{i}", f"DISHU{i}", "主菜", (), known_grams=False) for i in range(30)]
        self.assertEqual(reference_dish_cost(floors, prices, set(), has_staple=True), D(20))
        # 沒選主食時主食類菜色可入菜單，會一起估
        self.assertEqual(reference_dish_cost(floors, prices, set(), has_staple=False), D(2))

    def test_owned_ingredients_cost_nothing(self):
        floors, prices = build([dish(i, 30, food="PORK") for i in range(MIN_PRICED_DISHES)])
        floors = [RecipeFloor(f.recipe_id, f"DISH{i}", f.category, f.grams) for i, f in enumerate(floors)]
        self.assertEqual(reference_dish_cost(floors, prices, set(), has_staple=True), D(30))
        self.assertEqual(reference_dish_cost(floors, prices, {"PORK"}, has_staple=True), D(0))

    def test_too_few_priced_dishes_means_no_floor(self):
        floors, prices = build([dish(i, 50) for i in range(MIN_PRICED_DISHES - 1)])
        self.assertIsNone(reference_dish_cost(floors, prices, set(), has_staple=True))

    def test_same_dish_counts_once_at_its_cheapest_recipe(self):
        entries = [dish(i, 40) for i in range(MIN_PRICED_DISHES)]
        floors, prices = build(entries)
        floors += [RecipeFloor(f"Y{i}", "DISH0", "主菜", (("CHEAP", D(100)),)) for i in range(30)]
        prices["CHEAP"] = D("0.01")
        self.assertEqual(reference_dish_cost(floors, prices, set(), has_staple=True), D(1))


@requires_db
class BudgetFloorApiTests(unittest.TestCase):
    """問卷 API：預算低於下限列為待補項目，送出生成被拒；達到下限即可。"""

    @classmethod
    def setUpClass(cls):
        from db_support import migrate_fresh
        migrate_fresh()

    def setUp(self):
        from db_support import truncate_all
        from test_t08_meal_requests import T08ApiTests, full_draft
        truncate_all()
        helper = T08ApiTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.client, self.member = helper.client, helper.a
        self.full_draft = full_draft

    def test_budget_below_floor_blocks_submit(self):
        draft = self.full_draft([{"member_id": self.member["id"]}], budget="80", dish_count=3)
        with patch("app.services.budget_floor.minimum_budget", return_value=D(100)) as floor:
            created = self.client.post("/meal-requests", json=draft).json()
            self.assertEqual(created["budget_floor"], "100")
            issue = next(i for i in created["issues"] if i["reason"] == "BUDGET_TOO_LOW")
            self.assertEqual(issue["field"], "budget")
            self.assertIn("100 元", issue["message"])
            self.assertFalse(created["complete"])
            submit = self.client.post(f"/meal-requests/{created['id']}/submit", json={"version": created["version"]},
                                      headers={"Idempotency-Key": uuid.uuid4().hex})
            self.assertEqual(submit.status_code, 422)
            kwargs = floor.call_args.kwargs
            self.assertEqual(kwargs["dish_count"], 3)
            self.assertTrue(kwargs["has_staple"])

            raised = self.client.put(f"/meal-requests/{created['id']}",
                                     json={"version": created["version"], "draft": {**draft, "budget": "100"}}).json()
            self.assertTrue(raised["complete"], raised["issues"])

    def test_no_floor_does_not_block(self):
        draft = self.full_draft([{"member_id": self.member["id"]}], budget="1")
        with patch("app.services.budget_floor.minimum_budget", return_value=None):
            created = self.client.post("/meal-requests", json=draft).json()
        self.assertIsNone(created["budget_floor"])
        self.assertTrue(created["complete"], created["issues"])


if __name__ == "__main__":
    unittest.main()
