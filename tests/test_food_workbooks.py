"""Nutrition variant, provenance, transaction and versioning regression tests."""
from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path
import tempfile
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from openpyxl import Workbook
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (Allergen, FoodNutrition, IngredientAlias, NutritionImport, NutritionSource,
                        NutritionSourceLink, Recipe, RecipeIngredient, RecipeStep, SourceArchive)
from app.services.nutrition_import import (NUTRIENT_COLUMNS, find_nutrition_candidates,
                                          import_nutrition_bundle, parse_nutrition_workbooks)
from app.services.recipe_import import WorkbookValidationError
from recipe_workbook import build, recipe


def write_book(path, sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        headers = list(dict.fromkeys(key for row in rows for key in row))
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(key) for key in headers])
    book.save(path)


def observation(key, state, method=None, **changes):
    row = {column: 1 for column in NUTRIENT_COLUMNS}
    row.update({"Food_ID": "FOOD000005", "標準食材名稱": "高麗菜", "熟製狀態": state,
                "烹飪方式": method, "食用前處理": None, "計算方式": "合成測試",
                "原來源ID": key, "計算來源ID": key, "原來源筆數": 1, "計算來源筆數": 1})
    row.update(changes)
    return row


class FoodWorkbookTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.recipes = build(self.root / "recipes.xlsx", [recipe("R001", "炒高麗菜")],
                             {"FOOD000005": "高麗菜", "FOOD000006": "其他食材"})
        self.catalog = self.root / "catalog.xlsx"
        write_book(self.catalog, {"標準食材整合": [
            {"Food_ID": "FOOD000005", "合併標準食材名稱": "高麗菜"},
            {"Food_ID": "FOOD000006", "合併標準食材名稱": "其他食材"}]})
        self.nutrition = self.root / "nutrition.xlsx"
        self.rows = [observation("TFDA_A01", "未熟製", **{"糖(g/100g)": None, "鈉(mg/100g)": 0}),
                     observation("USDA_123", "已熟製", "水煮")]
        self.sources = [{**{column: 1 for column in NUTRIENT_COLUMNS},
                         "原來源ID": key, "資料來源": provider, "來源資料ID": record,
                         "來源網址": "https://example.invalid/source"}
                        for key, provider, record in [("TFDA_A01", "TFDA", "A01"), ("USDA_123", "USDA", "123")]]
        self.engine = create_engine("sqlite:///:memory:")
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.engine.dispose)
        models = [SourceArchive, IngredientAlias, Allergen, Recipe, RecipeStep, RecipeIngredient,
                  NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink]
        Base.metadata.create_all(self.engine, tables=[m.__table__ for m in models])

    def parse(self):
        write_book(self.nutrition, {"All": self.rows, "原始來源與判定": self.sources})
        return parse_nutrition_workbooks(self.recipes, self.catalog, self.nutrition)

    def save(self, bundle):
        with Session(self.engine) as session, session.begin():
            return import_nutrition_bundle(session, bundle)

    def count(self, model):
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(model))

    def test_variants_nulls_zero_and_exact_lookup(self):
        bundle = self.parse()
        self.save(bundle)
        self.assertEqual(self.count(FoodNutrition), 2)
        with Session(self.engine) as session:
            raw = find_nutrition_candidates(session, import_id=bundle.import_id, food_id="FOOD000005",
                                            cooking_state="未熟製", cooking_method=None)
            self.assertEqual(len(raw), 1)
            self.assertIsNone(raw[0].sugars_g)
            self.assertEqual(raw[0].sodium_mg, Decimal(0))
            self.assertFalse(raw[0].nutrition_complete)
            self.assertEqual(find_nutrition_candidates(session, import_id=bundle.import_id, food_id="FOOD000005",
                                                       cooking_state="已熟製", cooking_method="烤"), [])

    def test_reimport_does_not_duplicate_rows(self):
        bundle = self.parse()
        self.save(bundle)
        result = self.save(bundle)
        self.assertEqual(result["nutrition"]["status"], "already_imported")
        self.assertEqual(self.count(NutritionImport), 1)
        self.assertEqual(self.count(FoodNutrition), 2)
        self.assertEqual(self.count(NutritionSourceLink), 2)

    def test_changed_workbook_preserves_prior_values_and_sources(self):
        old = self.parse()
        self.save(old)
        self.rows[0]["蛋白質(g/100g)"] = 9
        new = self.parse()
        self.save(new)
        self.assertNotEqual(old.import_id, new.import_id)
        with Session(self.engine) as session:
            for batch, expected in [(old, 1), (new, 9)]:
                row = find_nutrition_candidates(session, import_id=batch.import_id, food_id="FOOD000005",
                                                cooking_state="未熟製", cooking_method=None)[0]
                self.assertEqual(row.protein_g, expected)
        self.assertEqual(self.count(NutritionSource), 4)

    def test_failure_rolls_back_recipe_and_nutrition_together(self):
        bundle = self.parse()
        broken = replace(bundle, observations=[{**bundle.observations[0], "food_id": "UNKNOWN"}])
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            self.save(broken)
        for model in (Recipe, IngredientAlias, NutritionImport, NutritionSource, FoodNutrition):
            self.assertEqual(self.count(model), 0)

    def test_name_differences_and_missing_ids_are_reported_without_renaming_catalog(self):
        self.rows[1]["標準食材名稱"] = "熟高麗菜"
        bundle = self.parse()
        self.assertEqual(bundle.report["counts"]["name_difference_records"], 1)
        self.assertEqual(bundle.report["counts"]["catalog_ids_without_nutrition"], 1)
        self.save(bundle)
        with Session(self.engine) as session:
            self.assertEqual(session.get(IngredientAlias, "FOOD000005").canonical_name, "高麗菜")

    def test_excluded_sources_are_linked_and_count_mismatch_is_preserved(self):
        self.rows[0].update({"原來源ID": "TFDA_A01\nUSDA_123", "原來源筆數": 2, "計算來源筆數": 2})
        bundle = self.parse()
        self.assertEqual(bundle.report["counts"]["source_count_mismatches"], 1)
        self.assertEqual(sum(not link["included_in_calculation"] for link in bundle.links), 1)

    def test_recipe_derived_source_keeps_original_quantity_basis(self):
        self.rows[0].update({"原來源ID": "R001-I1", "計算來源ID": "R001-I1"})
        bundle = self.parse()
        derived = next(s for s in bundle.sources if s["provider"] == "recipe_dataset")
        self.assertEqual(derived["raw_data"]["weight_g"], 300)
        self.assertIn("recipe_derived_nutrition_unreviewed", bundle.observations[0]["quality_issues"])

    def test_deduplication_keeps_nutrition_provenance_from_skipped_recipe(self):
        build(self.recipes, [recipe("R001", "炒高麗菜"), recipe("R002", "炒高麗菜")],
              {"FOOD000005": "高麗菜", "FOOD000006": "其他食材"})
        self.rows[0].update({"原來源ID": "R002-I1", "計算來源ID": "R002-I1"})
        bundle = self.parse()
        self.save(bundle)
        self.save(bundle)
        self.assertEqual(self.count(Recipe), 1)
        self.assertEqual(self.count(SourceArchive), 1)
        self.assertEqual(self.count(FoodNutrition), 2)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(RecipeIngredient, "R002-I1"))
            source = session.scalar(select(NutritionSource).where(NutritionSource.source_key == "R002-I1"))
            self.assertEqual(source.raw_data["recipe_id"], "R002")

    def test_existing_duplicate_is_removed_but_unrelated_recipes_are_protected(self):
        from app.services.recipe_import import parse_recipe_workbook
        build(self.recipes, [recipe("R001", "炒高麗菜"), recipe("R002", "炒高麗菜")],
              {"FOOD000005": "高麗菜", "FOOD000006": "其他食材"})
        full = parse_recipe_workbook(self.recipes)
        self.save(self.parse())
        # Simulate the previous importer, which retained the secondary recipe.
        with Session(self.engine) as session, session.begin():
            for model, rows in [(SourceArchive, full.source_archive), (Recipe, full.recipes),
                                (RecipeStep, full.recipe_steps), (RecipeIngredient, full.recipe_ingredients)]:
                for row in rows:
                    key = next(iter(model.__table__.primary_key)).name
                    if session.get(model, row[key]) is None:
                        session.add(model(**row))
                session.flush()
        result = self.save(self.parse())
        self.assertEqual(result["recipes"]["recipes"]["deleted"], 1)
        self.assertEqual(self.count(Recipe), 1)
        self.assertEqual(self.count(SourceArchive), 1)
        self.assertEqual(self.count(RecipeStep), 1)
        self.assertEqual(self.count(RecipeIngredient), 1)

    def test_rejects_dangling_source_and_duplicate_variant(self):
        self.rows[0]["原來源ID"] = "MISSING"
        with self.assertRaises(WorkbookValidationError):
            self.parse()
        self.rows[0]["原來源ID"] = "TFDA_A01"
        self.rows.append(dict(self.rows[0]))
        with self.assertRaises(WorkbookValidationError):
            self.parse()

    def test_rejects_invalid_numeric_and_dictionary_disagreement(self):
        for value in ("NaN", "Infinity", -1, "適量"):
            self.rows[0]["蛋白質(g/100g)"] = value
            with self.assertRaises(WorkbookValidationError):
                self.parse()
        self.rows[0]["蛋白質(g/100g)"] = 1
        write_book(self.catalog, {"標準食材整合": [{"Food_ID": "FOOD000005", "合併標準食材名稱": "甘藍"}]})
        with self.assertRaises(WorkbookValidationError):
            self.parse()

    def test_refuses_to_remove_existing_recipes(self):
        bundle = self.parse()
        self.save(bundle)
        modified = replace(bundle, recipe_bundle=replace(bundle.recipe_bundle, recipes=[]))
        with self.assertRaises(WorkbookValidationError):
            self.save(modified)
        self.assertEqual(self.count(Recipe), 1)

    def test_new_migration_upgrade_and_downgrade(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/0d52b9a6c831_versioned_food_nutrition.py"
        spec = importlib.util.spec_from_file_location("nutrition_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            for model in [NutritionSourceLink, FoodNutrition, NutritionSource, NutritionImport]:
                model.__table__.drop(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn("food_nutrition", inspect(connection).get_table_names())
                migration.downgrade()
            self.assertNotIn("food_nutrition", inspect(connection).get_table_names())
            self.assertIn("recipes", inspect(connection).get_table_names())
