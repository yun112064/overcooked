"""T01：食譜匯入的品質規則與重跑行為（AC05、G01）。工作簿皆為合成資料。"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from recipe_workbook import build, ingredient, recipe


def parse(recipes):
    from app.services.recipe_import import parse_recipe_workbook
    with tempfile.TemporaryDirectory() as folder:
        return parse_recipe_workbook(build(Path(folder) / "book.xlsx", recipes))


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

    def test_unreviewed_recipe_never_pads_the_count(self):
        bundle = parse([recipe("R001", "清炒高麗菜", approved=False)])
        self.assertFalse(by_id(bundle)["R001"]["is_eligible"])
        self.assertEqual(bundle.report["g01"]["status"], "not_passed")
        self.assertEqual(bundle.report["g01"]["gap"], 150)


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

    def test_reimport_with_renumbered_steps(self):
        from app.models import RecipeStep
        base = recipe("R001", "番茄炒蛋", steps=("切番茄", "炒蛋"))
        self.run_import([base], "a.xlsx")
        swapped = {**base, "step_ids": {1: "R001-S2", 2: "R001-S1"}}  # 兩個步驟交換編號
        self.run_import([swapped], "b.xlsx")
        with session_factory()() as session:
            numbers = dict(session.execute(select(RecipeStep.step_id, RecipeStep.step_no)).all())
        self.assertEqual(numbers, {"R001-S1": 2, "R001-S2": 1})


if __name__ == "__main__":
    unittest.main()
