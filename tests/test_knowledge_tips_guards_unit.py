"""knowledge-tips 路由的防線（直接呼叫路由函式，不連資料庫、不呼叫付費服務）。

- 營養通過但菜單不能採用（例如菜含禁吃食材）時不花 T10 額度
- 付費查詢每戶每分鐘有上限
- 不存在或已不合格的食譜回 409 MENU_CHANGED，不是 503
- rollback 後不再讀 ORM 屬性（讀了會重開交易，付費呼叫期間一直佔著連線）
"""
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# 設定物件要求 DATABASE_URL；這些測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.api import meal_requests as route
from app.api.errors import ApiError
from app.services import t12_planning
from app.services.assistant import RateLimiter

HOUSEHOLD = uuid.uuid4()
PASSED = {"status": "nutrition_passed", "adoption_blocked": False, "final_food_ids": ["FOOD000039"]}


class FakeDb:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


class ExpiringRow:
    """模擬 rollback 後過期的 ORM 物件：rollback 之後讀 version 就算失敗。"""

    def __init__(self, db):
        self._db = db
        self.household_id = HOUSEHOLD
        self.payload = {}

    @property
    def version(self):
        if self._db.rolled_back:
            raise AssertionError("rollback 後讀了 row.version（會重開交易）")
        return 3


class KnowledgeTipsGuardTests(unittest.TestCase):
    def call(self, menu=None, *, limiter=None, preview_error=None):
        db = FakeDb()
        body = route.NutritionPreviewBody(recipe_ids=["R1"])
        preview = MagicMock(return_value=menu, side_effect=preview_error)
        run = MagicMock(return_value={"status": "available", "items": []})
        with patch.object(route, "get_owned_request", return_value=ExpiringRow(db)), \
                patch.object(route.service, "load", return_value=SimpleNamespace()), \
                patch.object(route, "_reject_bad_references"), \
                patch.object(route, "_members", return_value={}), \
                patch.object(route.service, "completeness", return_value=[]), \
                patch.object(route.t12_planning, "preview", preview), \
                patch.object(route.ingredients, "names", return_value={}), \
                patch.object(route.knowledge_explanation, "tips_for_menu", run):
            result = route.knowledge_tips(uuid.uuid4(), body, household_id=HOUSEHOLD, db=db,
                                          limiter=limiter or RateLimiter(0))
        return result, run

    def test_adoption_blocked_menu_never_reaches_paid_calls(self):
        menu = {**PASSED, "adoption_blocked": True,
                "input_issues": [{"code": "DIETARY_CONFLICT", "adoption_blocked": True}]}
        result, run = self.call(menu)
        self.assertEqual((result["status"], result["reason"]), ("unavailable", "ADOPTION_BLOCKED"))
        self.assertEqual(result["final_food_ids"], [])
        run.assert_not_called()

    def test_missing_adoption_flag_fails_closed(self):
        result, run = self.call({"status": "nutrition_passed", "final_food_ids": ["FOOD000039"]})
        self.assertEqual(result["reason"], "ADOPTION_BLOCKED")
        run.assert_not_called()

    def test_nutrition_not_passed_keeps_its_reason(self):
        result, run = self.call({"status": "outside_tolerance", "adoption_blocked": True, "final_food_ids": []})
        self.assertEqual(result["reason"], "NUTRITION_NOT_PASSED")
        run.assert_not_called()

    def test_passing_menu_uses_the_version_read_before_rollback(self):
        result, run = self.call(PASSED)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["input_version"], 3)
        self.assertTrue(run.call_args.kwargs["request_tag"].endswith(":3"))

    def test_rate_limit_stops_paid_calls_per_household(self):
        limiter = RateLimiter(1)
        _, run = self.call(PASSED, limiter=limiter)
        run.assert_called_once()
        with self.assertRaises(ApiError) as caught:
            self.call(PASSED, limiter=limiter)
        self.assertEqual((caught.exception.status_code, caught.exception.code), (429, "RATE_LIMITED"))

    def test_blocked_menus_do_not_use_up_the_rate_limit(self):
        limiter = RateLimiter(1)
        self.call({**PASSED, "adoption_blocked": True}, limiter=limiter)
        _, run = self.call(PASSED, limiter=limiter)
        run.assert_called_once()

    def test_unknown_recipe_is_409_and_outage_stays_503(self):
        with self.assertRaises(ApiError) as caught:
            self.call(preview_error=t12_planning.T12MenuChanged("找不到可用的食譜（R1），請重新查詢菜單。"))
        self.assertEqual((caught.exception.status_code, caught.exception.code), (409, "MENU_CHANGED"))
        with self.assertRaises(ApiError) as caught:
            self.call(preview_error=t12_planning.T12PlanningUnavailable("RECIPE_DATA_UNAVAILABLE", "x"))
        self.assertEqual((caught.exception.status_code, caught.exception.code),
                         (503, "RECIPE_DATA_UNAVAILABLE"))


class MenuRejectionTests(unittest.TestCase):
    """「不採用，換一份」重送或連點同一組時不重複記錄；rejected_count 數不同組合。"""

    def test_same_set_is_recorded_once(self):
        from app.services import database_menu_plans as plans
        row = SimpleNamespace(id=uuid.uuid4(), version=2, status="draft")
        stored = []
        db = MagicMock()
        db.add.side_effect = stored.append
        with patch.object(plans, "_rejected", side_effect=lambda _db, _row: [set(r.recipe_ids) for r in stored]):
            plans.reject(db, row, version=2, recipe_ids=["R2", "R1"], user_id=None)
            plans.reject(db, row, version=2, recipe_ids=["R1", "R2", "R1"], user_id=None)
            plans.reject(db, row, version=2, recipe_ids=["R3"], user_id=None)
        self.assertEqual([r.recipe_ids for r in stored], [["R1", "R2"], ["R3"]])


if __name__ == "__main__":
    unittest.main()
