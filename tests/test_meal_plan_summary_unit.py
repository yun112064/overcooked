"""已採用餐食紀錄的摘要／明細／趨勢（services/meal_plans.py；不連資料庫）。

兩種快照：T14 生成結果採用（snapshot.result）與資料庫菜單營養預覽採用（snapshot.nutrition 等，
database_menu_plans._snapshot）。tests/test_t16_meal_plans.py 需要 PostgreSQL，本機只有這裡會執行。
"""
import os
import unittest
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

# 設定物件要求 DATABASE_URL；這些測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.services import meal_plans

HOUSEHOLD = uuid.uuid4()
DISHES = [{"standard_dish_id": "DISH-1", "dish_name": "清炒高麗菜", "aliases": [], "source": "database",
           "recipe_id": "R1"}]


def generation_snapshot(final=None):
    payload = {"schema_version": "generation_result/v1", "mode": "database_menu", "dishes": [
        {**DISHES[0], "specified": False, "steps": ["炒"], "steps_display": ["炒"]}],
        "calculation": {"total_n": "1", "ingredients": [], "final": final if final is not None else {}},
        "targets": {"rule_version": "t03", "total_n": "1", "target": {"energy_kcal": "600", "protein_g": "30"},
                    "items": []},
        "previous_day": {"previous_date": "2026-08-19"}, "staple": None, "cost": {}}
    return {"schema_version": "meal_plan_snapshot/v1", "result": payload,
            "request": {"meal_date": "2026-08-20", "meal_type": "dinner"}, "recipe_sources": {},
            "missing": {}, "previous_day": {"at_adoption": {}, "history_changed": False}, "versions": {}}


PREVIEW_SNAPSHOT = {"schema_version": "database_menu_snapshot/v2", "status": "nutrition_passed",
                    "nutrition": {"totals": {"energy_kcal": "590", "protein_g": "31"}},
                    "targets": {"energy_kcal": "600", "protein_g": "30"}, "selected": [], "market_shortages": {}}


def plan(snapshot, *, result_version=None, mode="database_menu", plan_id=None):
    return SimpleNamespace(id=plan_id or uuid.uuid4(), household_id=HOUSEHOLD, meal_date=date(2026, 8, 20),
                           meal_type="dinner", mode=mode, status="adopted",
                           adopted_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc), meal_request_id=uuid.uuid4(),
                           result_version=result_version, request_version=1, price_exception_accepted=False,
                           dishes=DISHES, snapshot=snapshot)


class FakeScalars(list):
    def all(self):
        return list(self)


class FakeDb:
    def __init__(self, plans):
        self.plans = plans

    def scalars(self, _stmt):
        return FakeScalars(self.plans)


class SummaryTests(unittest.TestCase):
    def test_generation_plan_keeps_result_version_and_unknown_energy_is_none(self):
        s = meal_plans.summary(plan(generation_snapshot(), result_version=2))
        self.assertEqual(s["result_version"], 2)
        self.assertEqual(s["status"], "adopted")
        self.assertIsNone(s["energy_kcal"])
        self.assertIsNone(s["protein_g"])
        self.assertFalse(s["market_shortages_available"])
        self.assertEqual(s["dishes"][0]["recipe_id"], "R1")
        self.assertNotIn("snapshot", s)  # 列表不展開整份快照

    def test_generation_plan_reads_energy_from_the_stored_calculation(self):
        s = meal_plans.summary(plan(generation_snapshot({"energy_kcal": "612.5", "protein_g": "33"}),
                                    result_version=1))
        self.assertEqual((s["energy_kcal"], s["protein_g"]), ("612.5", "33"))

    def test_preview_plan_reads_energy_from_the_stored_preview(self):
        s = meal_plans.summary(plan(PREVIEW_SNAPSHOT))
        self.assertIsNone(s["result_version"])
        self.assertEqual((s["energy_kcal"], s["protein_g"]), ("590", "31"))
        self.assertTrue(s["market_shortages_available"])
        self.assertEqual(s["mode_label"], "資料庫菜單")
        self.assertNotIn("snapshot", s)


class DetailTests(unittest.TestCase):
    def test_preview_plan_exposes_its_snapshot(self):
        d = meal_plans.detail(plan(PREVIEW_SNAPSHOT))
        self.assertTrue(d["snapshot_available"])
        self.assertEqual(d["snapshot"], PREVIEW_SNAPSHOT)

    def test_minimal_record_without_snapshot(self):
        d = meal_plans.detail(plan({}))
        self.assertFalse(d["snapshot_available"])
        self.assertEqual(d["snapshot"], {})

    def test_generation_plan_keeps_the_result_view_without_raw_snapshot(self):
        with patch.object(meal_plans, "object_session", return_value=None), \
                patch.object(meal_plans.meal_results, "nutrition_with_people", return_value={"per_person": []}):
            d = meal_plans.detail(plan(generation_snapshot(), result_version=1))
        self.assertTrue(d["snapshot_available"])
        self.assertNotIn("snapshot", d)
        self.assertEqual(d["result_version"], 1)


class TrendTests(unittest.TestCase):
    KEYS = {"meal_date", "meal_type", "energy_kcal", "protein_g", "target_energy_kcal", "target_protein_g",
            "cost_total", "known_cost_total", "budget"}

    def test_every_plan_returns_the_same_row_shape(self):
        preview = plan(PREVIEW_SNAPSHOT)
        minimal = plan({})
        generated = plan(generation_snapshot({"energy_kcal": "612.5", "protein_g": "33",
                                              "target": {"energy_kcal": "600", "protein_g": "30"}}),
                         result_version=1)
        items = meal_plans.trend(FakeDb([preview, minimal, generated]), HOUSEHOLD, date(2026, 8, 1),
                                 date(2026, 8, 31))
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(set(item), self.KEYS)
        by_energy = {i["energy_kcal"]: i for i in items}
        self.assertEqual(by_energy[590.0]["target_energy_kcal"], 600.0)
        self.assertIsNone(by_energy[590.0]["cost_total"])  # 沒有 cost 區：成本未知，不是 0
        self.assertEqual(by_energy["612.5"]["target_energy_kcal"], "600")
        empty = by_energy[None]
        self.assertTrue(all(empty[k] is None for k in self.KEYS - {"meal_date", "meal_type"}))

    def test_preview_plan_reports_the_cost_stored_at_adoption(self):
        cost = {"status": "computed", "budget": "300", "budget_status": "within", "price_complete": True,
                "purchase_total": "120", "cost_total": "85.5", "known_purchase_total": "120",
                "known_cost_total": "85.5", "lines": []}
        [item] = meal_plans.trend(FakeDb([plan({**PREVIEW_SNAPSHOT, "cost": cost})]), HOUSEHOLD,
                                  date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual((item["cost_total"], item["known_cost_total"], item["budget"]), ("85.5", "85.5", "300"))

    def test_preview_plan_with_uncomputed_cost_stays_unknown(self):
        snap = {**PREVIEW_SNAPSHOT, "cost": {"status": "not_computed", "reason": "NO_FINAL_REQUIREMENTS"}}
        [item] = meal_plans.trend(FakeDb([plan(snap)]), HOUSEHOLD, date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual((item["cost_total"], item["known_cost_total"], item["budget"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
