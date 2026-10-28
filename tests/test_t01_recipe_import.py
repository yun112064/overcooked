"""T01：食譜匯入的品質規則與重跑行為（AC05、G01）。工作簿皆為合成資料。"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from recipe_workbook import build, ingredient, recipe


SOURCE_RIGHTS = {"合成來源": {"license_name": "合成來源授權（測試）", "rights_status": "verified",
                            "source_version": "v1", "retrieved_at": "2026-10-17T00:00:00+08:00"}}


def parse(recipes, catalog=None, source_rights=None, review_record=None):
    from app.services.recipe_import import parse_recipe_workbook
    with tempfile.TemporaryDirectory() as folder:
        return parse_recipe_workbook(build(Path(folder) / "book.xlsx", recipes, catalog), source_rights, review_record)


def quantity(raw_text, amount, unit="g", name="高麗菜"):
    bundle = parse([recipe("R001", "清炒高麗菜", ingredients=[
        ingredient("FOOD000005", name, amount, raw_text=raw_text, unit=unit, weight_g=amount)])])
    return bundle.recipe_ingredients[0], by_id(bundle)["R001"]


def by_id(bundle):
    return {r["recipe_id"]: r for r in bundle.recipes}


class QualityRuleTests(unittest.TestCase):
    def test_fully_reviewed_recipe_qualifies(self):
        bundle = parse([recipe("R001", "清炒高麗菜")])
        self.assertTrue(by_id(bundle)["R001"]["is_eligible"], by_id(bundle)["R001"]["quality_issues"])
        self.assertEqual(bundle.report["g01"]["actual_qualified_recipes"], 1)

    def test_nonpositive_servings_is_stored_null_and_blocks(self):
        row = by_id(parse([recipe("R001", "清炒高麗菜", servings="0人份")]))["R001"]
        self.assertIsNone(row["original_servings"])
        self.assertEqual(row["servings_raw"], "0人份")
        self.assertIn("nonpositive_servings", row["quality_issues"])
        self.assertFalse(row["is_eligible"])

    def test_inverted_adjustable_range_is_stored_null_and_blocks(self):
        bundle = parse([recipe("R001", "清炒高麗菜", ingredients=[
            ingredient("FOOD000005", "高麗菜", 300, adjustable_min=400, adjustable_max=200)])])
        row = bundle.recipe_ingredients[0]
        self.assertEqual((row["adjustable_min"], row["adjustable_max"]), (None, None))
        self.assertFalse(by_id(bundle)["R001"]["is_eligible"])
        self.assertIn("ingredient:invalid_adjustable_range", by_id(bundle)["R001"]["quality_issues"])

    def test_bad_numeric_cell_only_affects_that_recipe(self):
        bundle = parse([
            recipe("R001", "清炒高麗菜", ingredients=[ingredient("FOOD000005", "高麗菜", 300, amount="適量", weight_g="很多")]),
            recipe("R002", "蒜炒青江菜", ingredients=[ingredient("FOOD000006", "青江菜", 250)]),
        ])
        rows = by_id(bundle)
        bad = bundle.recipe_ingredients[0]
        self.assertEqual((bad["source_amount"], bad["source_weight_g"]), (None, None))
        self.assertFalse(rows["R001"]["is_eligible"])
        self.assertIn("ingredient:invalid_numeric_value:amount", rows["R001"]["quality_issues"])
        self.assertTrue(rows["R002"]["is_eligible"], rows["R002"]["quality_issues"])

    def test_negative_amount_is_rejected_per_row(self):
        bundle = parse([recipe("R001", "清炒高麗菜", ingredients=[ingredient("FOOD000005", "高麗菜", -5)])])
        self.assertIsNone(bundle.recipe_ingredients[0]["amount"])
        self.assertFalse(by_id(bundle)["R001"]["is_eligible"])

    def test_exact_duplicates_count_once(self):
        bundle = parse([recipe("R002", "番茄炒蛋"), recipe("R001", "番茄炒蛋"), recipe("R003", "清炒高麗菜")])
        rows = by_id(bundle)
        self.assertTrue(rows["R001"]["is_eligible"])
        self.assertFalse(rows["R002"]["is_eligible"])
        self.assertIn("duplicate_not_primary", rows["R002"]["quality_issues"])
        self.assertEqual(rows["R002"]["duplicate_of_recipe_id"], "R001")
        self.assertEqual(bundle.report["g01"]["actual_qualified_recipes"], 2)

    def test_suspected_duplicates_count_once_until_reviewed(self):
        same = [ingredient("FOOD000001", "番茄", 200), ingredient("FOOD000002", "雞蛋", 150)]
        bundle = parse([recipe("R001", "番茄炒蛋", ingredients=same),
                        recipe("R002", "家常番茄炒蛋", ingredients=same)])
        rows = by_id(bundle)
        self.assertEqual(rows["R001"]["duplicate_status"], "suspected_duplicate")
        self.assertTrue(rows["R001"]["is_eligible"])
        self.assertFalse(rows["R002"]["is_eligible"])
        self.assertIn("suspected_duplicate_not_primary", rows["R002"]["quality_issues"])

    def test_catalog_sheet_becomes_the_food_dictionary(self):
        bundle = parse([recipe("R001", "清炒高麗菜")],
                       catalog={"FOOD000005": "高麗菜", "FOOD001566": "雞蛋", "FOOD000039": "大蒜"})
        aliases = {row["food_id"]: row["canonical_name"] for row in bundle.ingredient_aliases}
        self.assertEqual(aliases, {"FOOD000005": "高麗菜", "FOOD001566": "雞蛋", "FOOD000039": "大蒜"})
        self.assertEqual(bundle.report["counts"]["catalog_food_ids"], 3)

    def test_recipe_food_missing_from_catalog_is_rejected(self):
        from app.services.recipe_import import WorkbookValidationError
        with self.assertRaises(WorkbookValidationError):
            parse([recipe("R001", "清炒高麗菜")], catalog={"FOOD001566": "雞蛋"})
        with self.assertRaises(WorkbookValidationError):  # 同一編號名稱不一致
            parse([recipe("R001", "清炒高麗菜")], catalog={"FOOD000005": "甘藍菜"})

    def test_source_rights_config_fills_missing_rights_only(self):
        no_rights = [recipe("R001", "清炒高麗菜", rights_in_workbook=False)]
        before = by_id(parse(no_rights))["R001"]
        self.assertIn("missing_license", before["quality_issues"])
        self.assertFalse(before["is_eligible"])
        after = by_id(parse(no_rights, source_rights=SOURCE_RIGHTS))["R001"]
        for issue in ("missing_license", "rights_not_verified", "missing_source_version", "missing_retrieved_at"):
            self.assertNotIn(issue, after["quality_issues"])
        self.assertTrue(after["is_eligible"], after["quality_issues"])
        # 授權不能取代人工審核
        pending = by_id(parse([recipe("R001", "清炒高麗菜", rights_in_workbook=False, approved=False)],
                              source_rights=SOURCE_RIGHTS))["R001"]
        self.assertIn("recipe_review_pending", pending["quality_issues"])
        self.assertFalse(pending["is_eligible"])

    def test_vague_words_use_reference_amount(self):
        for raw in ("鹽 適量", "胡椒 少許", "雞腿 一大隻", "香菇 十幾朵", "鱸魚 數塊", "醬油"):
            row, recipe_row = quantity(raw, 10, name=raw.split()[0])
            self.assertEqual((row["quantity_status"], row["amount"]), ("reference_amount", 10), raw)
            self.assertTrue(recipe_row["is_eligible"], (raw, recipe_row["quality_issues"]))

    def test_reference_amount_needs_an_amount(self):
        row, recipe_row = quantity("鹽 適量", None, name="鹽")
        self.assertEqual(row["quantity_status"], "qualitative_unresolved")
        self.assertFalse(recipe_row["is_eligible"])

    def test_chinese_numerals_must_match_amount(self):
        cases = {"雞蛋 六顆": 6, "醬油露 半碗": 0.5, "高麗菜 一顆半": 1.5, "肉 十二片": 12, "米 兩杯": 2}
        for raw, value in cases.items():
            row, _ = quantity(raw, value, unit="顆", name=raw.split()[0])
            self.assertEqual(row["quantity_status"], "source_exact", raw)
        # 原文與 amount 不一致時以 Excel 的 amount 為準，只留下提醒、不阻擋
        row, recipe_row = quantity("雞蛋 六顆", 2, unit="顆", name="雞蛋")
        self.assertEqual((row["quantity_status"], row["amount"]), ("reference_amount", 2))
        self.assertIn("ingredient:raw_text_differs_from_amount", recipe_row["quality_issues"])
        self.assertTrue(recipe_row["is_eligible"], recipe_row["quality_issues"])

    def test_notes_and_names_are_not_read_as_quantities(self):
        row, _ = quantity("[內餡食材(六份)] 鮪魚罐頭 一罐", 1, unit="罐", name="鮪魚罐頭")
        self.assertEqual(row["quantity_status"], "source_exact")
        row, _ = quantity("九層塔(摘葉子就好) 半包", 0.5, unit="包", name="九層塔")
        self.assertEqual(row["quantity_status"], "source_exact")

    def test_review_record_applies_only_to_the_reviewed_workbook(self):
        from app.services.recipe_import import parse_recipe_workbook
        books = [recipe("R001", "清炒高麗菜", approved=None, rights_in_workbook=False)]
        with tempfile.TemporaryDirectory() as folder:
            path = build(Path(folder) / "book.xlsx", books)
            import hashlib
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            approvals = {key: "approved" for key in
                         ("source_review", "recipe_review", "cooking_method", "step_review", "adjustable_range")}
            record = {"workbook_sha256": digest, "reviewed_on": "2026-10-19", "approvals": approvals}
            ok = parse_recipe_workbook(path, SOURCE_RIGHTS, record)
            stale = parse_recipe_workbook(path, SOURCE_RIGHTS, {**record, "workbook_sha256": "0" * 64})
        self.assertTrue(by_id(ok)["R001"]["is_eligible"], by_id(ok)["R001"]["quality_issues"])
        self.assertTrue(ok.report["review_record"]["applied"])
        self.assertFalse(by_id(stale)["R001"]["is_eligible"])
        self.assertFalse(stale.report["review_record"]["applied"])

    def test_unreviewed_recipe_never_pads_the_count(self):
        bundle = parse([recipe("R001", "清炒高麗菜", approved=False)])
        self.assertFalse(by_id(bundle)["R001"]["is_eligible"])
        self.assertEqual(bundle.report["g01"]["status"], "not_passed")
        self.assertEqual(bundle.report["g01"]["gap"], 150)


OVERRIDES = {"R001": {"category": "湯", "reason": "合成：其實是湯", "decided_at": "2026-10-21"},
             "R404": {"category": "主菜", "reason": "合成：工作簿沒有這道", "decided_at": "2026-10-21"}}
REPO = Path(__file__).resolve().parents[1]
# 決策 #38：使用者 2026-10-21 確認的分類修正（主食→主菜 24 道、主食→點心 1 道）
TO_MAIN = ["R0020", "R0043", "R0088", "R0091", "R0092", "R0094", "R0095", "R0096", "R0098", "R0105", "R0106",
           "R0116", "R0117", "R0129", "R0130", "R0132", "R0137", "R0140", "R0144", "R0149", "R0180", "R0185",
           "R0188", "R0192"]


class CategoryOverrideTests(unittest.TestCase):
    def test_import_applies_overrides_without_touching_the_workbook(self):
        from app.services.recipe_import import parse_recipe_workbook
        with tempfile.TemporaryDirectory() as folder:
            path = build(Path(folder) / "book.xlsx", [recipe("R001", "番茄蛋花"), recipe("R002", "清炒高麗菜")])
            before = path.read_bytes()
            bundle = parse_recipe_workbook(path, category_overrides=OVERRIDES)
            self.assertEqual(path.read_bytes(), before)
        rows = by_id(bundle)
        self.assertEqual((rows["R001"]["category"], rows["R002"]["category"]), ("湯", "主菜"))
        report = bundle.report["category_overrides"]
        self.assertEqual(report["applied"], [{"recipe_id": "R001", "from": "主菜", "to": "湯", "reason": "合成：其實是湯",
                                              "decided_at": "2026-10-21"}])
        self.assertEqual(report["missing_recipe_ids"], ["R404"])

    def test_without_overrides_workbook_category_is_kept(self):
        self.assertEqual(by_id(parse([recipe("R001", "番茄蛋花")]))["R001"]["category"], "主菜")

    def test_config_holds_the_user_confirmed_corrections(self):
        from app.services.recipe_import import CATEGORY_OVERRIDES_PATH, load_category_overrides
        self.assertEqual(CATEGORY_OVERRIDES_PATH, REPO / "config" / "recipe_category_overrides.json")
        overrides = load_category_overrides()
        # 決策 #54 追加的修正（→主食／點心／主菜／湯）
        decision_54 = {**{r: "主食" for r in ("R0073", "R0074", "R0075", "R0076", "R0077", "R0078", "R0079", "R0081",
                                               "R0084", "R0121", "R0142", "R0196", "R0212", "R0272", "R0279",
                                               "R0280", "R0282", "R0004")},
                       "R0012": "點心", "R0143": "主菜", "R0152": "主菜", "R0200": "湯", "R0257": "湯", "R0276": "湯"}
        expected = {**{r: "主菜" for r in TO_MAIN}, "R0173": "點心", **decision_54}
        self.assertEqual({k: o["category"] for k, o in overrides.items()}, expected)
        self.assertEqual(overrides["R0140"]["category"], "主菜")
        self.assertTrue(all(o["decided_at"] == "2026-10-21" and o["reason"] for o in overrides.values()))

    def test_every_override_changes_the_category_of_a_real_workbook_recipe(self):
        from openpyxl import load_workbook
        from app.services.recipe_import import load_category_overrides
        wb = load_workbook(REPO / "data" / "recipe_dataset_1027.xlsx", read_only=True, data_only=True)
        try:
            rows = wb["recipes"].iter_rows(values_only=True)
            header = list(next(rows))
            rid, cat, name = header.index("recipe_id"), header.index("category"), header.index("recipe_name")
            book = {r[rid]: (r[name], r[cat]) for r in rows if r[rid]}
        finally:
            wb.close()
        self.assertEqual(book["R0140"], ("蕃茄炒蛋", "主食"))
        for recipe_id, override in load_category_overrides().items():
            name, category = book[recipe_id]
            self.assertEqual(override["recipe_name"], name, recipe_id)
            self.assertNotEqual(override["category"], category, recipe_id)  # 每筆都確實改變分類


@requires_db
class CategoryOverrideDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
        bundle = parse_recipe_workbook(build(Path(self.folder.name) / "book.xlsx",
                                             [recipe("R001", "番茄蛋花"), recipe("R002", "清炒高麗菜")]))
        with session_factory()() as session, session.begin():
            import_recipe_bundle(session, bundle)

    def apply(self, **kwargs):
        from app.services.recipe_import import apply_category_overrides
        with session_factory()() as session, session.begin():
            return apply_category_overrides(session, OVERRIDES, **kwargs)

    def category(self, recipe_id):
        from app.models import Recipe
        with session_factory()() as session:
            return session.get(Recipe, recipe_id).category

    def test_applies_to_existing_database_idempotently(self):
        dry = self.apply(dry_run=True)
        self.assertEqual([c["recipe_id"] for c in dry["changed"]], ["R001"])
        self.assertEqual(self.category("R001"), "主菜")  # 試跑不寫入
        first = self.apply()
        self.assertEqual(first["changed"], [{"recipe_id": "R001", "from": "主菜", "to": "湯", "reason": "合成：其實是湯",
                                             "decided_at": "2026-10-21"}])
        self.assertEqual(first["missing_recipe_ids"], ["R404"])
        self.assertEqual((self.category("R001"), self.category("R002")), ("湯", "主菜"))
        second = self.apply()
        self.assertEqual((second["changed"], second["unchanged"]), ([], ["R001"]))

    def test_command_reads_the_config_file(self):
        import json
        from app.jobs.apply_category_overrides import run
        path = Path(self.folder.name) / "overrides.json"
        path.write_text(json.dumps({"overrides": {"R002": OVERRIDES["R001"]}}, ensure_ascii=False), encoding="utf-8")
        result = run(session_factory(), path)
        self.assertEqual([c["recipe_id"] for c in result["changed"]], ["R002"])
        self.assertEqual(self.category("R002"), "湯")


@requires_db
class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def run_import(self, recipes, name="book.xlsx"):
        from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
        bundle = parse_recipe_workbook(build(Path(self.folder.name) / name, recipes))
        with session_factory()() as session, session.begin():
            return import_recipe_bundle(session, bundle)

    def count(self, model, **where):
        with session_factory()() as session:
            query = select(func.count()).select_from(model)
            for key, value in where.items():
                query = query.where(getattr(model, key) == value)
            return session.scalar(query)

    def test_invalid_values_no_longer_roll_back_the_whole_batch(self):
        from app.models import Recipe, RecipeIngredient
        self.run_import([
            recipe("R001", "清炒高麗菜", servings="0人份"),
            recipe("R002", "蒜炒青江菜", ingredients=[
                ingredient("FOOD000006", "青江菜", 250, adjustable_min=300, adjustable_max=100)]),
            recipe("R003", "番茄炒蛋"),
        ])
        self.assertEqual(self.count(Recipe), 3)
        self.assertEqual(self.count(Recipe, is_eligible=True), 1)
        self.assertEqual(self.count(RecipeIngredient), 3)

    def test_duplicate_listed_before_primary_imports(self):
        from app.models import Recipe
        self.run_import([recipe("R002", "番茄炒蛋"), recipe("R001", "番茄炒蛋")])
        with session_factory()() as session:
            self.assertEqual(session.get(Recipe, "R002").duplicate_of_recipe_id, "R001")

    def test_reimport_is_idempotent(self):
        from app.models import RecipeIngredient, RecipeStep
        books = [recipe("R001", "清炒高麗菜", steps=("切菜", "下鍋炒"))]
        first = self.run_import(books, "a.xlsx")
        second = self.run_import(books, "b.xlsx")
        self.assertEqual(first["recipe_steps"], {"inserted": 2, "updated": 0, "deleted": 0})
        self.assertEqual(second["recipe_steps"], {"inserted": 0, "updated": 2, "deleted": 0})
        self.assertEqual((self.count(RecipeStep), self.count(RecipeIngredient)), (2, 1))

    def test_reimport_removes_rows_dropped_from_workbook(self):
        from app.models import RecipeIngredient, RecipeStep
        self.run_import([recipe("R001", "番茄炒蛋", steps=("切番茄", "炒蛋", "拌炒"), ingredients=[
            ingredient("FOOD000001", "番茄", 200), ingredient("FOOD000002", "雞蛋", 150)])], "a.xlsx")
        result = self.run_import([recipe("R001", "番茄炒蛋", steps=("切番茄", "拌炒"), ingredients=[
            ingredient("FOOD000001", "番茄", 200)])], "b.xlsx")
        self.assertEqual(result["recipe_steps"]["deleted"], 1)
        self.assertEqual(result["recipe_ingredients"]["deleted"], 1)
        self.assertEqual((self.count(RecipeStep), self.count(RecipeIngredient)), (2, 1))

    def test_reimport_deletes_recipes_removed_from_workbook(self):
        from app.models import Recipe, RecipeIngredient, RecipeStep, SourceArchive
        self.run_import([recipe("R001", "清炒高麗菜"), recipe("R002", "番茄炒蛋", steps=("切", "炒"), ingredients=[
            ingredient("FOOD000001", "番茄", 200)])], "a.xlsx")
        result = self.run_import([recipe("R001", "清炒高麗菜")], "b.xlsx")
        self.assertEqual(result["recipes"]["deleted"], 1)
        self.assertEqual(result["source_archive"]["deleted"], 1)
        self.assertEqual(result["recipe_steps"]["deleted"], 2)
        self.assertEqual(result["recipe_ingredients"]["deleted"], 1)
        self.assertEqual(self.count(Recipe), 1)
        self.assertEqual(self.count(RecipeStep, recipe_id="R002"), 0)
        self.assertEqual(self.count(RecipeIngredient, recipe_id="R002"), 0)
        self.assertEqual(self.count(SourceArchive), 1)
        # 刪除後再加回來也能正常匯入
        again = self.run_import([recipe("R001", "清炒高麗菜"), recipe("R002", "番茄炒蛋")], "c.xlsx")
        self.assertEqual((again["recipes"]["inserted"], again["recipes"]["deleted"]), (1, 0))

    def test_reimport_with_renumbered_steps(self):
        from app.models import RecipeStep
        base = recipe("R001", "番茄炒蛋", steps=("切番茄", "炒蛋"))
        self.run_import([base], "a.xlsx")
        swapped = {**base, "step_ids": {1: "R001-S2", 2: "R001-S1"}}  # 兩個步驟交換編號
        self.run_import([swapped], "b.xlsx")
        with session_factory()() as session:
            numbers = dict(session.execute(select(RecipeStep.step_id, RecipeStep.step_no)).all())
        self.assertEqual(numbers, {"R001-S1": 2, "R001-S2": 1})


class WorkbookRevisionTests(unittest.TestCase):
    """1027 版工作簿：步驟欄改名為 instruction、recipes 表尾端多出空白欄。"""

    def revise(self, path, extra_value=None):
        from openpyxl import load_workbook
        wb = load_workbook(path)
        wb["recipe_steps"]["D1"] = "instruction"
        recipes = wb["recipes"]
        width = recipes.max_column
        for col in range(width + 1, width + 4):
            recipes.cell(row=1, column=col, value="")
        if extra_value is not None:
            recipes.cell(row=2, column=width + 2, value=extra_value)
        wb.save(path)
        return path

    def test_renamed_step_column_and_trailing_blank_columns_are_accepted(self):
        from app.services.recipe_import import parse_recipe_workbook
        with tempfile.TemporaryDirectory() as folder:
            path = self.revise(build(Path(folder) / "book.xlsx", [recipe("R001", "清炒高麗菜", steps=("步驟一：熱鍋",))]))
            bundle = parse_recipe_workbook(path)
        self.assertEqual([s["instruction_clean"] for s in bundle.recipe_steps], ["步驟一：熱鍋"])  # 原文不改

    def test_data_in_an_unnamed_column_is_rejected(self):
        from app.services.recipe_import import WorkbookValidationError, parse_recipe_workbook
        with tempfile.TemporaryDirectory() as folder:
            path = self.revise(build(Path(folder) / "book.xlsx", [recipe("R001", "清炒高麗菜")]), extra_value="備註")
            with self.assertRaisesRegex(WorkbookValidationError, "unnamed column"):
                parse_recipe_workbook(path)

    def test_real_1027_workbook_parses_with_the_review_record(self):
        from app.services.recipe_import import load_review_record, load_source_rights, parse_recipe_workbook
        bundle = parse_recipe_workbook(REPO / "data" / "recipe_dataset_1027.xlsx",
                                       load_source_rights(REPO / "config" / "recipe_sources.json"),
                                       load_review_record(REPO / "config" / "recipe_reviews.json"))
        self.assertEqual(len(bundle.recipes), 293)
        self.assertEqual(len(bundle.recipe_steps), 1471)
        self.assertTrue(all(s["review_status"] == "approved" for s in bundle.recipe_steps))
        self.assertTrue(all(r["review_status"] == "approved" for r in bundle.recipes))


if __name__ == "__main__":
    unittest.main()
