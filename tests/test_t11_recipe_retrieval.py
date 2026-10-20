"""T11：結構化食譜查詢與昨日排除（R04、R13、AC06、AC07、AC09、AC30、AC31）。

食譜皆為合成測試資料，直接寫入 T01 資料表；預期結果逐項由人工依情境寫定（AC06 固定情境）。
"""
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db_support import migrate_fresh, requires_db, seed_foods, session_factory, truncate_all

EGG, TOMATO, CABBAGE, GARLIC, BOKCHOY, CARROT, BASIL, SOY = (
    "FOOD001566", "FOOD001016", "FOOD000867", "FOOD000039", "FOOD000585", "FOOD000678", "FOOD000004", "FOOD001541")

# recipe_id, 名稱, 別名, 食材, 烹調方法, 是否合格
RECIPES = [
    ("R01", "番茄炒蛋", ["蕃茄炒蛋"], [TOMATO, EGG], ["炒"], True),
    ("R02", "清炒高麗菜", ["炒高麗菜", "炒青菜"], [CABBAGE, GARLIC], ["炒"], True),
    ("R03", "蒜炒青江菜", ["炒青菜"], [BOKCHOY, GARLIC], ["炒"], True),
    ("R04", "紅蘿蔔炒蛋", [], [CARROT, EGG], ["炒"], True),
    ("R05", "塔香煎蛋", [], [BASIL, EGG], ["煎"], True),
    ("R06", "醬燒高麗菜", [], [CABBAGE, SOY], ["燒"], True),
    ("R07", "九層塔炒番茄", [], [BASIL, TOMATO], ["炒"], False),  # 未通過審核
    ("R08", "番茄炒蛋", [], [TOMATO, EGG], ["炒"], True),  # 另一來源的同一道菜（同標準菜色 ID）
]


def fix_tie_break_order(test):
    """固定情境（AC06）的期望值以人工寫定：同分時改依 recipe_id 排，讓情境可讀。

    正式的「家庭＋餐食日期」輪替（決策 #22）由 MenuCompositionTests 另外驗證。
    """
    patcher = patch("app.services.recipe_retrieval.tie_break", lambda household_id, meal_date, recipe_id: recipe_id)
    patcher.start()
    test.addCleanup(patcher.stop)


def seed_recipes(factory, rows=RECIPES):
    from app.models import Recipe, RecipeIngredient, SourceArchive
    from app.services.recipe_import import normalize_name, standard_dish_id
    with factory() as db, db.begin():
        for rid, name, aliases, foods, methods, eligible, *rest in rows:
            category = rest[0] if rest else "主菜"  # 第 7 欄可指定菜色分類，預設主菜
            db.add(SourceArchive(raw_id=f"RAW-{rid}", source_name="合成來源", source_url=f"https://example.invalid/{rid}",
                                 source_recipe_id=rid, recipe_name_raw=name, servings_raw="2人份",
                                 ingredients_raw="", steps_raw="", tags_raw="", content_hash="x" * 64,
                                 import_batch_id="t11"))
            db.flush()
            db.add(Recipe(recipe_id=rid, raw_id=f"RAW-{rid}", standard_dish_id=standard_dish_id(name), recipe_name=name,
                          normalized_name=normalize_name(name), name_aliases=aliases, servings_raw="2人份",
                          original_servings=2, servings_status="parsed", category=category, meal_type="晚餐",
                          cuisine="台式", source_name="合成來源", source_url=f"https://example.invalid/{rid}",
                          cooking_methods=methods, is_eligible=eligible,
                          review_status="approved" if eligible else "pending"))
            db.flush()
            for seq, food in enumerate(foods, start=1):
                db.add(RecipeIngredient(recipe_ingredient_id=f"{rid}-I{seq}", recipe_id=rid, sequence=seq,
                                        raw_text=food, canonical_name=food, food_id=food, amount=100, unit="g",
                                        quantity_status="parsed", unit_status="parsed", weight_g=100,
                                        weight_status="parsed"))


def dish(name, aliases=(), source="database"):
    from app.services.recipe_import import standard_dish_id
    return {"standard_dish_id": standard_dish_id(name), "dish_name": name, "aliases": list(aliases), "source": source}


# 名稱, 問卷欄位, 排除食材, 昨日（本家）菜, 預期
SCENARIOS = [
    ("01 無限制：依編號取兩道不同菜", {"dish_count": 2}, [], [], ("database_menu", ["R01", "R02"])),
    ("02 已有食材優先", {"dish_count": 1, "available_ingredient_ids": [BOKCHOY]}, [], [], ("database_menu", ["R03"])),
    ("03 口味偏好為軟性排序", {"dish_count": 1, "taste_preferences": "想吃煎的"}, [], [], ("database_menu", ["R05"])),
    ("04 口味偏好無對應仍有菜", {"dish_count": 1, "taste_preferences": "想吃泰式"}, [], [], ("database_menu", ["R01"])),
    ("05 指定菜名計入菜數", {"dish_count": 2, "specified_dishes": ["蒜炒青江菜"]}, [], [], ("database_menu", ["R03", "R01"])),
    ("06 指定菜以別名對應", {"dish_count": 1, "specified_dishes": ["蕃茄炒蛋"]}, [], [], ("database_menu", ["R01"])),
    ("07 指定菜名正規化（空白、全形）", {"dish_count": 1, "specified_dishes": ["番茄 炒蛋"]}, [], [],
     ("database_menu", ["R01"])),
    ("08 指定菜不在資料庫 → AI 回退", {"dish_count": 2, "specified_dishes": ["麻婆豆腐"]}, [], [],
     ("no_feasible_database_menu", "SPECIFIED_DISH_NOT_IN_DATABASE")),
    ("09 指定菜只有未審核食譜 → 視為不在資料庫", {"dish_count": 1, "specified_dishes": ["九層塔炒番茄"]}, [], [],
     ("no_feasible_database_menu", "SPECIFIED_DISH_NOT_IN_DATABASE")),
    ("10 指定菜別名對到兩道不同菜 → 回報無法判定", {"dish_count": 1, "specified_dishes": ["炒青菜"]}, [], [],
     ("dish_identity_unresolved", "SPECIFIED_DISH_AMBIGUOUS")),
    ("11 過敏食材排除", {"dish_count": 3}, [EGG], [], ("database_menu", ["R02", "R03", "R06"])),
    ("12 排除後不足菜數 → AI 回退", {"dish_count": 4}, [EGG], [],
     ("no_feasible_database_menu", "NO_MATCHING_RECIPES")),
    ("13 指定菜含過敏食材：回報衝突，不放寬也不轉 AI", {"dish_count": 1, "specified_dishes": ["番茄炒蛋"]}, [EGG], [],
     ("specified_dish_conflict", "SPECIFIED_DISH_DIETARY_CONFLICT")),
    ("14 排除多種禁吃食材", {"dish_count": 2}, [CABBAGE, TOMATO], [], ("database_menu", ["R03", "R04"])),
    ("15 排除昨日菜", {"dish_count": 2}, [], [dish("番茄炒蛋")], ("database_menu", ["R02", "R03"])),
    ("16 昨日 AI 菜以別名判同菜（含同 ID 的另一來源）", {"dish_count": 2}, [], [dish("蕃茄炒蛋", source="ai")],
     ("database_menu", ["R02", "R03"])),
    ("17 昨日菜以別名記錄仍排除", {"dish_count": 1}, [], [dish("炒高麗菜")], ("database_menu", ["R01"])),
    ("18 明確指定昨日菜可重複，其他昨日菜仍排除", {"dish_count": 2, "specified_dishes": ["番茄炒蛋"]}, [],
     [dish("番茄炒蛋"), dish("清炒高麗菜")], ("database_menu", ["R01", "R03"])),
    ("19 排除昨日菜後不足 → 去重原因回退", {"dish_count": 3}, [],
     [dish("番茄炒蛋"), dish("清炒高麗菜"), dish("蒜炒青江菜"), dish("紅蘿蔔炒蛋")],
     ("no_feasible_database_menu", "ALL_EXCLUDED_BY_PREVIOUS_DAY")),
    ("20 禁忌加昨日排除後不足 → 去重原因回退", {"dish_count": 3}, [EGG], [dish("清炒高麗菜")],
     ("no_feasible_database_menu", "ALL_EXCLUDED_BY_PREVIOUS_DAY")),
    ("21 合格菜色總數不足", {"dish_count": 7}, [], [], ("no_feasible_database_menu", "NO_MATCHING_RECIPES")),
    ("22 同標準菜色只選一次，六道不同菜全選", {"dish_count": 6}, [], [],
     ("database_menu", ["R01", "R02", "R03", "R04", "R05", "R06"])),
    ("23 指定菜加禁忌：其他菜仍避開過敏", {"dish_count": 2, "specified_dishes": ["醬燒高麗菜"]}, [GARLIC], [],
     ("database_menu", ["R06", "R01"])),
]


@requires_db
class RetrievalScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.db.models import Household
        fix_tie_break_order(self)
        truncate_all()
        seed_foods()
        self.factory = session_factory()
        seed_recipes(self.factory)
        with self.factory() as db, db.begin():
            self.home, self.other = Household(name="本家"), Household(name="別家")
            db.add_all([self.home, self.other])

    def adopt(self, household, dishes, day=date(2026, 8, 19), status="adopted"):
        from app.services import meal_history
        with self.factory() as db, db.begin():
            meal_history.record_adopted(db, household.id, day, "dinner", mode="database_menu", dishes=dishes,
                                        status=status)

    def retrieve(self, fields, excluded=(), factory=None):
        from app.services.meal_request import MealRequestDraft
        from app.services.recipe_retrieval import retrieve_database_menu
        draft = MealRequestDraft(meal_date=date(2026, 8, 20), meal_type="dinner", **fields)
        with (factory or self.factory)() as db:
            return retrieve_database_menu(db, self.home.id, draft, excluded_food_ids=list(excluded))

    def assert_outcome(self, result, expected):
        status, detail = expected
        self.assertEqual(result.status, status, result.reason)
        if status == "database_menu":
            self.assertEqual([d["recipe_id"] for d in result.selected], detail)
            self.assertIsNone(result.reason)
        else:
            self.assertEqual(result.reason, detail)
            self.assertEqual(result.selected, [])

    def test_fixed_scenarios(self):
        self.assertGreaterEqual(len(SCENARIOS), 20)
        for name, fields, excluded, yesterday, expected in SCENARIOS:
            with self.subTest(name):
                truncate_history()
                if yesterday:
                    self.adopt(self.home, yesterday)
                self.assert_outcome(self.retrieve(fields, excluded), expected)

    def test_specified_dish_conflict_names_the_dish_and_ingredients(self):
        result = self.retrieve({"dish_count": 2, "specified_dishes": ["番茄炒蛋"]}, excluded=[EGG])
        self.assertEqual(result.code, "SPECIFIED_DISH_DIETARY_CONFLICT")
        self.assertEqual(result.details["conflicts"], [{"dish_name": "番茄炒蛋", "food_ids": [EGG]}])

    def test_alternative_combinations_are_ranked_and_bounded(self):
        # 第一組即原本的選擇；其餘依排名換掉較後面的菜，且每組菜數相同、不重複、不超過上限
        from app.services.recipe_retrieval import MAX_COMBINATIONS
        result = self.retrieve({"dish_count": 2})
        combos = [[d["recipe_id"] for d in c] for c in result.alternatives]
        self.assertEqual(combos[0], ["R01", "R02"])
        self.assertEqual(combos[1], ["R01", "R03"])
        self.assertTrue(all(len(c) == 2 and len(set(c)) == 2 for c in combos))
        self.assertEqual(len({tuple(c) for c in combos}), len(combos))
        self.assertLessEqual(len(combos), MAX_COMBINATIONS)
        specified = self.retrieve({"dish_count": 2, "specified_dishes": ["醬燒高麗菜"]})
        self.assertTrue(all(c[0]["recipe_id"] == "R06" for c in specified.alternatives))

    def test_selected_dishes_carry_identity_and_specified_flag(self):
        result = self.retrieve({"dish_count": 2, "specified_dishes": ["蕃茄炒蛋"]})
        first = result.selected[0]
        self.assertEqual((first["recipe_name"], first["specified"]), ("番茄炒蛋", True))
        self.assertIn("蕃茄炒蛋", first["aliases"])
        self.assertFalse(result.selected[1]["specified"])
        self.assertEqual(len({d["standard_dish_id"] for d in result.selected}), 2)

    def test_candidates_exclude_restricted_previous_day_and_ineligible_recipes(self):
        self.adopt(self.home, [dish("清炒高麗菜")])
        result = self.retrieve({"dish_count": 1}, excluded=[SOY])
        ids = {c["recipe_id"] for c in result.candidates}
        self.assertEqual(ids, {"R01", "R03", "R04", "R05", "R08"})

    def test_result_records_previous_day_context(self):
        self.adopt(self.home, [dish("番茄炒蛋"), dish("清炒高麗菜")])
        result = self.retrieve({"dish_count": 2, "specified_dishes": ["番茄炒蛋"]})
        ctx = result.previous_day
        self.assertEqual((ctx["meal_date"], ctx["previous_date"]), ("2026-08-20", "2026-08-19"))
        self.assertEqual([d["dish_name"] for d in ctx["excluded_dishes"]], ["清炒高麗菜"])
        self.assertEqual(len(ctx["specified_exceptions"]), 1)
        self.assertEqual(result.to_dict()["previous_day"], ctx)

    def test_no_feasible_result_is_fallback_with_context(self):
        self.adopt(self.home, [dish("番茄炒蛋")])
        result = self.retrieve({"dish_count": 2, "specified_dishes": ["麻婆豆腐"]})
        data = result.to_dict()
        self.assertEqual(data["code"], "NO_FEASIBLE_DATABASE_MENU")
        self.assertEqual(data["details"]["specified_dishes"], ["麻婆豆腐"])
        # AI 回退仍須避開未指定的昨日菜：排除清單隨結果一起交給生成
        self.assertEqual([d["dish_name"] for d in data["previous_day"]["excluded_dishes"]], ["番茄炒蛋"])

    def test_other_household_and_unadopted_history_do_not_exclude(self):
        self.adopt(self.other, [dish("番茄炒蛋")])
        self.adopt(self.home, [dish("清炒高麗菜")], status="superseded")
        self.adopt(self.home, [dish("蒜炒青江菜")], day=date(2026, 8, 18))
        result = self.retrieve({"dish_count": 3})
        self.assertEqual([d["recipe_id"] for d in result.selected], ["R01", "R02", "R03"])

    def test_empty_recipe_database_is_no_match_not_error(self):
        truncate_all()
        from app.db.models import Household
        with self.factory() as db, db.begin():
            self.home = Household(name="本家")
            db.add(self.home)
        result = self.retrieve({"dish_count": 1})
        self.assertEqual((result.status, result.reason), ("no_feasible_database_menu", "NO_MATCHING_RECIPES"))

    def test_database_failure_is_an_error_not_no_result(self):
        from app.services.recipe_retrieval import RecipeRetrievalError
        broken = sessionmaker(bind=create_engine(
            "postgresql+psycopg://nobody:wrong@127.0.0.1:1/none", connect_args={"connect_timeout": 2}))
        with self.assertRaises(RecipeRetrievalError) as caught:
            self.retrieve({"dish_count": 1}, factory=broken)
        self.assertEqual(caught.exception.code, "RECIPE_DATABASE_UNAVAILABLE")

    def test_incomplete_draft_is_rejected(self):
        with self.assertRaises(ValueError):
            self.retrieve({})
        with self.assertRaises(ValueError):
            self.retrieve({"dish_count": 1, "specified_dishes": ["番茄炒蛋", "蒜炒青江菜"]})


# 決策 #22：菜色結構。每道都含已有的蛋＝重疊數全部 1，舊排序同分時照 recipe_id 會選到前三道主食
MENU_RECIPES = [
    ("M01", "沙茶羊肉燴飯", [], [EGG, SOY], ["燴"], True, "主食"),
    ("M02", "沙茶牛肉燴飯", [], [EGG, GARLIC], ["燴"], True, "主食"),
    ("M03", "蛋炒飯", [], [EGG, CARROT], ["炒"], True, "主食"),
    ("M04", "羅宋湯", [], [EGG, TOMATO], ["煮"], True, "湯"),
    ("M05", "蛋花湯", [], [EGG, BOKCHOY], ["煮"], True, "湯"),
    ("M06", "雞蛋糕", [], [EGG, BASIL], ["烤"], True, "點心"),
    ("M07", "洋蔥炒蛋", [], [EGG, CABBAGE], ["炒"], True, "主菜"),
    ("M08", "蒸蛋", [], [EGG, GARLIC], ["蒸"], True, "主菜"),
    ("M09", "菜脯蛋", [], [EGG, SOY], ["煎"], True, "主菜"),
    ("M10", "清燙高麗菜", [], [CABBAGE], ["燙"], True, "主菜"),
    ("M11", "白煮蛋", [], [EGG], ["煮"], True, "主菜"),  # 重疊 1、比例 100%
]
EXTRA_SOUPS = [("S01", "番茄湯", [], [TOMATO], ["煮"], True, "湯"),
               ("S02", "青江菜湯", [], [BOKCHOY], ["煮"], True, "湯")]


@requires_db
class MenuCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.db.models import Household
        truncate_all()
        seed_foods()
        self.factory = session_factory()
        seed_recipes(self.factory, MENU_RECIPES)
        with self.factory() as db, db.begin():
            self.home = Household(name="本家")
            db.add(self.home)

    def retrieve(self, fields, day=date(2026, 8, 20), excluded=()):
        from app.services.meal_request import MealRequestDraft
        from app.services.recipe_retrieval import retrieve_database_menu
        draft = MealRequestDraft(meal_date=day, meal_type="dinner", **fields)
        with self.factory() as db:
            return retrieve_database_menu(db, self.home.id, draft, excluded_food_ids=list(excluded))

    def assert_structure(self, combo, *, staple=True):
        cats = [d["category"] for d in combo]
        if staple:
            self.assertNotIn("主食", cats, combo)
        self.assertNotIn("點心", cats, combo)
        self.assertGreaterEqual(cats.count("主菜"), 1, combo)
        self.assertLessEqual(cats.count("湯"), 1, combo)

    def test_reported_case_staple_rice_three_dishes_gets_structured_menu(self):
        for offset in range(7):
            with self.subTest(day=offset):
                result = self.retrieve({"dish_count": 3, "staple": "白飯", "available_ingredient_ids": [EGG]},
                                       day=date(2026, 8, 20 + offset))
                self.assertEqual(result.status, "database_menu", result.reason)
                self.assertEqual(len(result.selected), 3)
                for combo in result.alternatives:
                    self.assert_structure(combo)

    def test_owned_count_then_owned_share_rank_before_tie_break(self):
        for offset in range(7):
            day = date(2026, 8, 20 + offset)
            with self.subTest(day=offset):
                # 重疊數優先：蛋＋高麗菜都有的洋蔥炒蛋（2 種）勝過比例 100% 的白煮蛋（1 種）
                both = self.retrieve({"dish_count": 1, "staple": "白飯", "available_ingredient_ids": [EGG, CABBAGE]},
                                     day=day)
                self.assertEqual([d["recipe_id"] for d in both.selected], ["M07"])
                # 重疊數同為 1 時比例高者優先：白煮蛋（1/1）勝過其他含蛋菜（1/2）
                share = self.retrieve({"dish_count": 1, "staple": "白飯", "available_ingredient_ids": [EGG]}, day=day)
                self.assertEqual([d["recipe_id"] for d in share.selected], ["M11"])

    def test_ties_rotate_by_household_and_date_but_are_reproducible(self):
        fields = {"dish_count": 1, "staple": "白飯"}
        picks = {}
        for offset in range(10):
            day = date(2026, 8, 1 + offset)
            first = self.retrieve(fields, day=day)
            self.assertEqual(first.alternatives, self.retrieve(fields, day=day).alternatives)  # 同一請求可重現
            picks[day] = first.selected[0]["recipe_id"]
        self.assertGreater(len(set(picks.values())), 1, picks)  # 不同日期換菜，不再固定最小編號

    def test_at_most_one_soup_even_when_soups_rank_highest(self):
        seed_recipes(self.factory, EXTRA_SOUPS)
        fields = {"dish_count": 2, "staple": "白飯", "available_ingredient_ids": [TOMATO, BOKCHOY]}
        result = self.retrieve(fields)
        # 兩道湯（S01、S02 比例 100%）排最前，但只能取一道，另一道補主菜
        self.assertIn(result.selected[0]["recipe_id"], {"S01", "S02"})
        self.assertEqual(result.selected[1]["category"], "主菜")
        for combo in result.alternatives:
            self.assert_structure(combo)

    def test_specified_soup_uses_up_the_soup_slot(self):
        seed_recipes(self.factory, EXTRA_SOUPS)
        result = self.retrieve({"dish_count": 3, "staple": "白飯", "specified_dishes": ["羅宋湯"],
                                "available_ingredient_ids": [TOMATO, BOKCHOY]})
        self.assertEqual(result.selected[0]["recipe_id"], "M04")
        for combo in result.alternatives:
            self.assertEqual([d["category"] for d in combo].count("湯"), 1, combo)
            self.assertIn("主菜", [d["category"] for d in combo])

    def test_without_staple_staple_category_dishes_may_be_chosen(self):
        result = self.retrieve({"dish_count": 3, "staple": "不需要主食", "available_ingredient_ids": [EGG]})
        self.assertEqual(result.status, "database_menu")
        chosen = {d["category"] for c in result.alternatives for d in c}
        self.assertIn("主食", chosen)
        for combo in result.alternatives:
            self.assert_structure(combo, staple=False)

    def test_unmet_structure_is_no_feasible_with_reason(self):
        seed_recipes(self.factory, EXTRA_SOUPS)
        # 排除蛋與高麗菜後只剩兩道湯（主食類因選了白飯不列入、點心不放正餐）→ 湊不出至少一道主菜
        result = self.retrieve({"dish_count": 2, "staple": "白飯"}, excluded=[EGG, CABBAGE])
        self.assertEqual((result.status, result.reason), ("no_feasible_database_menu", "MENU_STRUCTURE_UNMET"))
        self.assertEqual(result.code, "NO_FEASIBLE_DATABASE_MENU")
        self.assertEqual(result.selected, [])

    def test_specified_staple_dish_with_chosen_staple_is_reported(self):
        result = self.retrieve({"dish_count": 2, "staple": "白飯", "specified_dishes": ["蛋炒飯"]})
        self.assertEqual(result.status, "specified_dish_staple_conflict")
        self.assertEqual(result.code, "SPECIFIED_DISH_STAPLE_CONFLICT")
        self.assertEqual(result.details, {"specified_dishes": ["蛋炒飯"], "staple": "白飯"})
        self.assertEqual(result.selected, [])


def truncate_history():
    from db_support import _engine
    from sqlalchemy import text
    with _engine.begin() as conn:
        conn.execute(text("TRUNCATE adopted_meal_plans"))


if __name__ == "__main__":
    unittest.main()
