"""Feature G：結果頁價格區的無法計價食材、菜色封面網址與體積透傳（不連資料庫）。

cost 區由 generation.cost_snapshot 從合成的 T13 compute_meal_cost 結果產生（與 T14 保存的形狀相同），
不手寫猜測鍵名。T15／T16 的結果測試需要 PostgreSQL，本檔是本機唯一會執行的 _prices 回歸測試。
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.meal_calc.types import IngredientRequirement  # noqa: E402
from app.services.generation import _jsonable, cost_snapshot  # noqa: E402
from app.services.meal_cost import compute_meal_cost  # noqa: E402
from app.services.meal_results import (  # noqa: E402
    QUOTE_FIELDS, _ingredient, _prices, _source_name, dishes_with_covers, staple_view,
)
from app.services.meal_validation import evaluate  # noqa: E402
from app.source_adapters.price_models import PriceQuote  # noqa: E402

NOW = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
CABBAGE = "FOOD000867"
CARROT = "FOOD000678"
EGG = "FOOD001566"


def quote(food_id, price, qty="300", unit="g"):
    return PriceQuote(
        canonical_name=f"食材{food_id}", food_ids=[food_id],
        source_url="https://shop.pxgo.com.tw/search?q=x", queried_at=NOW - timedelta(hours=3),
        region_condition="未選地區", store_condition="未選門市",
        status="success", selection_tier="auto_adopted", confidence="high",
        product_id="P1", product_name=f"商品{food_id} {qty}{unit}", price=Decimal(price), currency="TWD",
        package_quantity=Decimal(qty), package_unit=unit, package_description=f"{qty}{unit}/包",
        product_url="https://shop.pxgo.com.tw/product/P1", availability="in_stock", price_kind="displayed",
    )


def missing_quote(food_id):
    return PriceQuote(canonical_name=f"食材{food_id}", food_ids=[food_id],
                      source_url="https://shop.pxgo.com.tw/search?q=x", queried_at=NOW,
                      status="not_found", missing_reason="not_found 說明")


def req(food_id, grams):
    return IngredientRequirement(food_id=food_id, grams=Decimal(grams))


def snapshot(quotes, requirements, budget="100"):
    """與 T14 保存的形狀相同：cost_snapshot 後經結果快照的 _jsonable（Decimal 轉字串、報價轉 dict）。"""
    return _jsonable(cost_snapshot(compute_meal_cost(requirements, set(), Decimal(budget), quotes.get, now=NOW)))


INGREDIENTS = [{"food_id": CABBAGE, "name": "高麗菜"}, {"food_id": CARROT, "name": "胡蘿蔔"},
               {"food_id": EGG, "name": "雞蛋"}]


def legacy_prices(payload: dict) -> dict:
    """改動前 DEMO 的 _prices（照原樣保留），作為沒有無法計價食材時的對照。"""
    cost = payload.get("cost") or {}
    if cost.get("status") != "computed":
        return {"status": cost.get("status", "not_computed"), "reason": cost.get("reason"), "budget": None,
                "budget_status": None, "price_complete": False, "purchase_total": None, "cost_total": None,
                "known_purchase_total": None, "known_cost_total": None, "items": []}
    names = {r["food_id"]: r["name"] for r in (payload.get("calculation") or {}).get("ingredients") or []}
    items = []
    for ln in cost["lines"]:
        if ln["price_status"] not in ("known", "unknown"):
            continue
        q = ln.get("quote") or {}
        items.append({"food_id": ln["food_id"], "name": names.get(ln["food_id"], ln["food_id"]),
                      "shortage_grams": ln["shortage_grams"], "price_status": ln["price_status"],
                      "packs": ln.get("packs"), "purchase_price": ln.get("purchase_price"), "cost": ln.get("cost"),
                      "missing_reason": ln.get("missing_reason"), "source_name": _source_name(q),
                      **{k: q.get(k) for k in QUOTE_FIELDS}})
    return {"status": "computed", **{k: cost.get(k) for k in (
        "budget", "budget_status", "price_complete", "purchase_total", "cost_total", "known_purchase_total",
        "known_cost_total")}, "items": items}


class PriceProjectionTests(unittest.TestCase):
    """移植自 MAIN tests/test_t13_meal_cost.py PriceProjectionTests。"""

    def test_unknown_quantity_hides_complete_totals_but_preserves_known_cost(self):
        payload = {
            "cost": {"status": "computed", "price_complete": True, "purchase_total": "60",
                     "cost_total": "20", "known_purchase_total": "60", "known_cost_total": "20",
                     "budget_status": "OK", "lines": [], "unquantified_food_ids": [CARROT]},
            "calculation": {"ingredients": [{"food_id": CARROT, "name": "胡蘿蔔"}]},
        }
        result = _prices(payload)
        self.assertFalse(result["price_complete"])
        self.assertIsNone(result["purchase_total"])
        self.assertIsNone(result["cost_total"])
        self.assertIsNone(result["budget_status"])
        self.assertEqual(result["known_purchase_total"], "60")
        self.assertEqual(result["known_cost_total"], "20")
        self.assertEqual(result["unquantified_items"], [{"food_id": CARROT, "name": "胡蘿蔔"}])
        payload["cost"]["budget_status"] = "BUDGET_EXCEEDED"
        self.assertEqual(_prices(payload)["budget_status"], "BUDGET_EXCEEDED")


class SnapshotPriceTests(unittest.TestCase):
    def payload(self, cost):
        return {"cost": cost, "calculation": {"ingredients": INGREDIENTS}}

    def complete_cost(self):
        return snapshot({CABBAGE: quote(CABBAGE, "60")}, [req(CABBAGE, "100")])

    def test_without_unquantified_ids_matches_pre_change_projection(self):
        for cost in (self.complete_cost(),
                     snapshot({CABBAGE: quote(CABBAGE, "60"), CARROT: missing_quote(CARROT)},
                              [req(CABBAGE, "100"), req(CARROT, "50")])):
            cost["unquantified_food_ids"] = []  # generation._cost_view 一定寫入這個鍵
            payload = self.payload(cost)
            before, after = legacy_prices(payload), _prices(payload)
            self.assertEqual(set(after) - set(before), {"unquantified_items"})
            self.assertEqual(after["unquantified_items"], [])
            self.assertEqual({k: v for k, v in after.items() if k not in ("items", "unquantified_items")},
                             {k: v for k, v in before.items() if k != "items"})
            self.assertEqual(len(after["items"]), len(before["items"]))
            for new, old in zip(after["items"], before["items"]):
                self.assertEqual(set(new) - set(old), {"shortage_ml"})
                self.assertIsNone(new["shortage_ml"])
                self.assertEqual({k: v for k, v in new.items() if k != "shortage_ml"}, old)

    def test_old_results_without_the_key_still_project(self):
        payload = self.payload(self.complete_cost())
        self.assertNotIn("unquantified_food_ids", payload["cost"])
        result = _prices(payload)
        self.assertEqual(result["unquantified_items"], [])
        self.assertTrue(result["price_complete"])
        self.assertEqual(result["purchase_total"], "60")
        self.assertEqual(result["budget_status"], "OK")

    def test_unquantified_ids_make_totals_unknown_not_zero(self):
        cost = self.complete_cost()
        cost["unquantified_food_ids"] = [EGG]
        result = _prices(self.payload(cost))
        self.assertFalse(result["price_complete"])
        self.assertIsNone(result["purchase_total"])
        self.assertIsNone(result["cost_total"])
        self.assertIsNone(result["budget_status"])
        self.assertEqual(result["known_purchase_total"], "60")
        self.assertEqual(result["known_cost_total"], "20")
        self.assertEqual(result["unquantified_items"], [{"food_id": EGG, "name": "雞蛋"}])
        self.assertTrue(cost["price_complete"])  # 只改投影，不改保存的快照

    def test_unknown_price_stays_none(self):
        cost = snapshot({CARROT: missing_quote(CARROT)}, [req(CARROT, "50")])
        cost["unquantified_food_ids"] = []
        result = _prices(self.payload(cost))
        [item] = result["items"]
        self.assertEqual(item["price_status"], "unknown")
        self.assertIsNone(item["purchase_price"])
        self.assertIsNone(item["cost"])
        self.assertIsNone(item["price"])
        self.assertFalse(result["price_complete"])
        self.assertIsNone(result["purchase_total"])
        self.assertIsNone(result["cost_total"])

    def test_not_computed_branch_also_lists_unquantified(self):
        result = _prices({"cost": {"status": "not_computed", "reason": "NO_FINAL_REQUIREMENTS",
                                   "budget_status": None, "unquantified_food_ids": [EGG]},
                          "calculation": {"ingredients": INGREDIENTS}})
        self.assertEqual(result["unquantified_items"], [{"food_id": EGG, "name": "雞蛋"}])
        self.assertIsNone(result["purchase_total"])
        self.assertFalse(result["price_complete"])

    def test_validation_reads_snapshot_not_projection(self):
        cost = self.complete_cost()
        cost["unquantified_food_ids"] = []
        payload = {"request": {"meal_request_id": "m1", "request_version": 1},
                   "validation": {"hard_constraints_passed": True},
                   "calculation": {"adoptable": True, "block_reasons": [], "ingredients": INGREDIENTS},
                   "cost": cost}
        before = evaluate(payload, decisions=[], current_request_version=1)
        _prices(payload)
        self.assertEqual(evaluate(payload, decisions=[], current_request_version=1), before)
        self.assertTrue(before["adoptable"])


class FakeSession:
    def __init__(self, rows):
        self.rows, self.statements = rows, []

    def execute(self, statement):
        self.statements.append(statement)
        return list(self.rows)


class CoverTests(unittest.TestCase):
    def dishes(self):
        return [{"source": "database", "recipe_id": "R0001", "dish_name": "番茄炒蛋"},
                {"source": "database", "recipe_id": "R0002", "dish_name": "炒高麗菜"},
                {"source": "ai", "recipe_id": None, "dish_name": "AI 湯"},
                {"source": "ai", "recipe_id": "R0001", "dish_name": "AI 同代碼"}]

    def test_cover_url_only_for_database_dish_with_row(self):
        db = FakeSession([("R0001", "recipe-1103-v3/dd4f")])
        out = dishes_with_covers(db, self.dishes())
        self.assertEqual(out[0]["cover_url"], "/recipe-covers/recipe-1103-v3%2Fdd4f/R0001")
        self.assertIsNone(out[1]["cover_url"])
        self.assertIsNone(out[2]["cover_url"])
        self.assertIsNone(out[3]["cover_url"])
        [statement] = db.statements
        params = statement.compile().params
        self.assertEqual(sorted(v for value in params.values() for v in value), ["R0001", "R0002"])

    def test_no_database_dishes_skips_query_and_keeps_key(self):
        db = FakeSession([])
        out = dishes_with_covers(db, [{"source": "ai", "recipe_id": None, "dish_name": "AI 湯"}])
        self.assertEqual(db.statements, [])
        self.assertIn("cover_url", out[0])
        self.assertIsNone(out[0]["cover_url"])


class VolumeTests(unittest.TestCase):
    def test_volume_ml_key_present_and_none(self):
        row = {"food_id": CABBAGE, "name": "高麗菜", "whole_meal_grams": "100", "display_grams": "100"}
        self.assertIn("volume_ml", _ingredient(row))
        self.assertIsNone(_ingredient(row)["volume_ml"])
        staple = staple_view({"staple": {"label": "白飯", "food_id": "FOOD000001"},
                              "calculation": {"ingredients": [{**row, "food_id": "FOOD000001", "name": "白飯",
                                                               "role": "staple"}]}})
        self.assertIn("volume_ml", staple)
        self.assertIsNone(staple["volume_ml"])


if __name__ == "__main__":
    unittest.main()
