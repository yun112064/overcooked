"""T18 額度驗證：多人並行＋重試不能繞過 T10 預留；額度不足時停止付費生成但歷史仍可查。

只用假的付費呼叫（不連 OpenAI），帳本寫在測試資料庫的合成月份。
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "load"))

from db_support import make_client, migrate_fresh, requires_db, session_factory, truncate_all  # noqa: E402

PASSWORD = "correct horse battery staple"


@requires_db
class SpendGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.factory = session_factory()

    def test_concurrent_users_and_retries_never_exceed_the_cap(self):
        import spend_guard
        report = spend_guard.run_spend_guard_check(
            self.factory, users=5, calls_per_user=12, month="2099-12",
            limit_twd=Decimal("2000"), committed_twd=Decimal("1998"))
        self.assertTrue(report["passed"], report)
        checks = {c["name"]: c for c in report["checks"]}
        for name in ("ledger_never_over_limit", "duplicate_call_key_refused", "budget_exhausted_reached",
                     "ledger_matches_reservations", "one_reservation_per_call_key"):
            self.assertTrue(checks[name]["passed"], checks[name])
        self.assertGreater(report["counts"]["budget_exhausted"], 0)
        self.assertGreater(report["counts"]["settled"], 0)

    def test_synthetic_month_is_cleaned_up(self):
        import spend_guard
        from sqlalchemy import text
        spend_guard.run_spend_guard_check(self.factory, users=5, calls_per_user=2, month="2099-11",
                                          limit_twd=Decimal("2000"), committed_twd=Decimal("1990"))
        with self.factory() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM budget_months WHERE month='2099-11'")).scalar(), 0)
            self.assertEqual(db.execute(text("SELECT count(*) FROM usage_reservations WHERE month='2099-11'")).scalar(), 0)

    def test_check_refuses_the_dev_database(self):
        import spend_guard
        with self.assertRaises(ValueError):
            spend_guard.assert_safe_database("postgresql+psycopg://u:p@127.0.0.1:55432/meal_planner_dev")
        spend_guard.assert_safe_database("postgresql+psycopg://u:p@127.0.0.1:55432/meal_planner_t18")

    def test_exhausted_budget_blocks_new_generation_but_history_stays_readable(self):
        from sqlalchemy import text
        from app.services import spend
        client = make_client()
        client.post("/auth/register", json={"email": "t18@example.test", "password": PASSWORD})
        client.post("/household", json={"name": "T18 家庭"})
        member = client.post("/members", json={
            "display_name": "成員", "birth_date": "1996-03-01", "equation_sex": "M", "weight_kg": "70",
            "activity_category": "sedentary", "goal": "maintain", "portion": "1",
            "allergen_ingredient_ids": [], "avoided_ingredient_ids": []}).json()
        req = client.post("/meal-requests", json={
            "meal_date": "2026-12-02", "meal_type": "dinner", "budget": "300", "staple": "白飯",
            "dish_count": 2, "participants": [{"member_id": member["id"]}]}).json()
        with self.factory() as db, db.begin():
            spend.ensure_month(db, spend.month_key())
            db.execute(text("UPDATE budget_months SET spent_twd = limit_twd - committed_twd WHERE month = :m"),
                       {"m": spend.month_key()})
        res = client.post(f"/meal-requests/{req['id']}/submit", json={"version": req["version"]},
                          headers={"Idempotency-Key": "t18-exhausted-1"})
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "SERVICE_BUDGET_EXHAUSTED"))
        self.assertEqual(client.get("/meal-plans", params={"date": "2026-12-01"}).status_code, 200)
        self.assertEqual(client.get("/meal-plans").status_code, 200)
        self.assertEqual(client.get(f"/meal-requests/{req['id']}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
