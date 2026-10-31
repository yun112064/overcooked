"""T11：家庭已採用餐食紀錄與前一日菜色比對（R13、AC30、AC31）。紀錄皆為測試資料。"""
import unittest
import uuid
from datetime import date

from db_support import migrate_fresh, requires_db, session_factory, truncate_all


def dish(name, aliases=(), source="database", recipe_id=None):
    from app.services.recipe_import import standard_dish_id
    return {"standard_dish_id": standard_dish_id(name), "dish_name": name, "aliases": list(aliases),
            "source": source, "recipe_id": recipe_id}


@requires_db
class PreviousDayContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.db.models import Household
        truncate_all()
        self.factory = session_factory()
        with self.factory() as db, db.begin():
            self.home, self.other = Household(name="本家"), Household(name="別家")
            db.add_all([self.home, self.other])

    def adopt(self, household, day, meal_type, dishes, status="adopted", mode="database_menu"):
        from app.services import meal_history
        with self.factory() as db, db.begin():
            return meal_history.record_adopted(db, household.id, day, meal_type, mode=mode, dishes=dishes,
                                               status=status)

    def context(self, day=date(2026, 8, 20), specified=()):
        from app.services import meal_history
        with self.factory() as db:
            return meal_history.previous_day_context(db, self.home.id, day, specified_dish_ids=list(specified))

    def excluded_names(self, ctx):
        return sorted(d["dish_name"] for d in ctx["excluded_dishes"])

    def test_reads_every_meal_type_of_the_previous_calendar_day_only(self):
        self.adopt(self.home, date(2026, 8, 19), "breakfast", [dish("蔥花蛋")])
        self.adopt(self.home, date(2026, 8, 19), "dinner", [dish("番茄炒蛋"), dish("清炒高麗菜")])
        self.adopt(self.home, date(2026, 8, 18), "dinner", [dish("麻婆豆腐")])  # 前天：不納入
        self.adopt(self.home, date(2026, 8, 20), "lunch", [dish("滷肉")])  # 當天：不納入
        ctx = self.context()
        self.assertEqual(ctx["meal_date"], "2026-08-20")
        self.assertEqual(ctx["previous_date"], "2026-08-19")
        self.assertEqual(self.excluded_names(ctx), ["清炒高麗菜", "番茄炒蛋", "蔥花蛋"])
        self.assertEqual(sorted(r["meal_type"] for r in ctx["records"]), ["breakfast", "dinner"])
        self.assertTrue(all(r["version"] == 1 and r["id"] for r in ctx["records"]))

    def test_other_household_and_not_adopted_records_are_ignored(self):
        self.adopt(self.other, date(2026, 8, 19), "dinner", [dish("番茄炒蛋")])
        self.adopt(self.home, date(2026, 8, 19), "lunch", [dish("宮保雞丁")], status="superseded")
        ctx = self.context()
        self.assertEqual((ctx["records"], ctx["excluded_dishes"]), ([], []))

    def test_no_previous_day_record_does_not_fall_back_to_earlier_dates(self):
        self.adopt(self.home, date(2026, 8, 17), "dinner", [dish("番茄炒蛋")])
        ctx = self.context()
        self.assertEqual(ctx["excluded_dishes"], [])
        self.assertEqual(ctx["previous_date"], "2026-08-19")

    def test_month_boundary_uses_calendar_day(self):
        self.adopt(self.home, date(2026, 2, 28), "dinner", [dish("番茄炒蛋")])
        self.assertEqual(self.excluded_names(self.context(day=date(2026, 3, 1))), ["番茄炒蛋"])

    def test_specified_dish_only_exempts_itself(self):
        tomato, cabbage = dish("番茄炒蛋"), dish("清炒高麗菜")
        self.adopt(self.home, date(2026, 8, 19), "dinner", [tomato, cabbage])
        ctx = self.context(specified=[tomato["standard_dish_id"]])
        self.assertEqual(self.excluded_names(ctx), ["清炒高麗菜"])
        self.assertEqual(ctx["specified_exceptions"], [tomato["standard_dish_id"]])

    def test_same_dish_listed_twice_yesterday_is_excluded_once(self):
        self.adopt(self.home, date(2026, 8, 19), "lunch", [dish("番茄炒蛋")])
        self.adopt(self.home, date(2026, 8, 19), "dinner", [dish("番茄炒蛋", aliases=["蕃茄炒蛋"])])
        ctx = self.context()
        self.assertEqual(self.excluded_names(ctx), ["番茄炒蛋"])
        self.assertEqual(ctx["excluded_dishes"][0]["aliases"], ["蕃茄炒蛋"])

    def test_invalid_mode_or_dish_without_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.adopt(self.home, date(2026, 8, 19), "dinner", [{"dish_name": "番茄炒蛋"}])
        with self.assertRaises(ValueError):
            self.adopt(self.home, date(2026, 8, 19), "dinner", [dish("番茄炒蛋")], mode="guess")


@requires_db
class AdoptionRecheckTests(unittest.TestCase):
    """AC31：採用前歷史變動須重新比對，拒絕失效結果。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.db.models import Household
        truncate_all()
        self.factory = session_factory()
        with self.factory() as db, db.begin():
            self.home = Household(name="本家")
            db.add(self.home)

    def adopt(self, day, dishes):
        from app.services import meal_history
        with self.factory() as db, db.begin():
            meal_history.record_adopted(db, self.home.id, day, "dinner", mode="database_menu", dishes=dishes)

    def recheck(self, ctx, dishes):
        from app.services import meal_history
        with self.factory() as db:
            return meal_history.recheck_previous_day(db, ctx, dishes)

    def context(self, specified=()):
        from app.services import meal_history
        with self.factory() as db:
            return meal_history.previous_day_context(db, self.home.id, date(2026, 8, 20),
                                                     specified_dish_ids=list(specified))

    def test_unchanged_history_passes(self):
        self.adopt(date(2026, 8, 19), [dish("番茄炒蛋")])
        ctx = self.context()
        self.assertEqual(self.recheck(ctx, [dish("清炒高麗菜")]), {"status": "ok"})

    def test_history_adopted_after_generation_conflicts(self):
        ctx = self.context()
        self.adopt(date(2026, 8, 19), [dish("清炒高麗菜", aliases=["炒高麗菜"])])
        result = self.recheck(ctx, [dish("炒高麗菜")])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["code"], "PREVIOUS_DAY_DISH_CONFLICT")
        self.assertEqual([d["dish_name"] for d in result["conflicting_dishes"]], ["炒高麗菜"])
        self.assertTrue(result["history_changed"])

    def test_history_change_without_dish_clash_is_still_reported_as_stale(self):
        ctx = self.context()
        self.adopt(date(2026, 8, 19), [dish("麻婆豆腐")])
        result = self.recheck(ctx, [dish("清炒高麗菜")])
        self.assertEqual(result, {"status": "ok", "history_changed": True,
                                  "context": result["context"]})
        self.assertEqual([d["dish_name"] for d in result["context"]["excluded_dishes"]], ["麻婆豆腐"])

    def test_specified_exception_survives_recheck(self):
        tomato = dish("番茄炒蛋")
        self.adopt(date(2026, 8, 19), [tomato])
        ctx = self.context(specified=[tomato["standard_dish_id"]])
        self.assertEqual(self.recheck(ctx, [tomato])["status"], "ok")

    def test_specified_dish_repeating_yesterday_under_its_alias_survives_recheck(self):
        specified = dish("清炒高麗菜", aliases=["炒高麗菜"])
        self.adopt(date(2026, 8, 19), [dish("炒高麗菜")])  # 昨日紀錄用別名、沒有別名清單
        ctx = self.context(specified=[specified["standard_dish_id"]])
        self.assertEqual(self.recheck(ctx, [specified])["status"], "ok")

    def test_alias_exception_only_covers_the_specified_dish(self):
        from app.services import meal_history
        specified = dish("清炒高麗菜", aliases=["炒高麗菜"])
        other = dish("高麗菜炒蛋", aliases=["炒高麗菜"])
        self.adopt(date(2026, 8, 19), [dish("炒高麗菜")])
        ctx = self.context(specified=[specified["standard_dish_id"]])
        self.assertEqual(meal_history.conflicts(ctx, [specified, other]), [other])

    def test_alias_repeat_is_shown_as_specified_repeat(self):
        from app.services.meal_results import _previous_day
        specified = dish("清炒高麗菜", aliases=["炒高麗菜"])
        self.adopt(date(2026, 8, 19), [dish("炒高麗菜"), dish("麻婆豆腐")])
        ctx = self.context(specified=[specified["standard_dish_id"]])
        shown = _previous_day({"previous_day": ctx, "dishes": [specified, dish("番茄炒蛋")]})
        self.assertEqual(shown["specified_repeats"], ["清炒高麗菜"])


if __name__ == "__main__":
    unittest.main()
