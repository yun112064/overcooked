from datetime import datetime, timezone
from decimal import Decimal
import pytest
import unittest

from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (Allergen, IngredientAlias, Recipe, RecipeDataRelease,
                        SourceArchive, RecipeIngredient)
from app.models.nutrition import (FoodNutrition, NutritionFoodMapping,
                                 NutritionImport, NutritionSource, T04CookingReceipt,
                                 NutritionSourceLink)
from app.services.meal_calculation import IngredientLine, versioned_sql_calculator
from app.services.meal_calculation import MealTargets, calculate_meal
from fractions import Fraction
from app.services import staples

pytestmark = pytest.mark.usefixtures("staple_policy")


class VersionedSQLNutritionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        tables = [RecipeDataRelease, SourceArchive, IngredientAlias, Allergen, Recipe, RecipeIngredient,
                  NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink,
                  NutritionFoodMapping, T04CookingReceipt]
        Base.metadata.create_all(self.engine, tables=[model.__table__ for model in tables])
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        release_hash = "a" * 64
        self.db.add(RecipeDataRelease(release_id="recipe-test-v1", dataset_version="test-v1",
            source_sha256=release_hash, schema_version="test", source_kind="xlsx_import",
            row_counts={}, active=True))
        self.db.flush()
        self.db.add(IngredientAlias(food_id="FOOD000296", recipe_release_id="recipe-test-v1", canonical_name="白飯",
            aliases=[], ambiguous_aliases=[], review_status="approved", quality_issues=[]))
        self.db.flush()
        source_files = {
            "recipes": {"name": "recipes.xlsx", "sha256": release_hash},
            "catalog": {"name": "catalog.xlsx", "sha256": "b" * 64},
            "nutrition": {"name": "nutrition.xlsx", "sha256": "c" * 64},
        }
        self.db.add(NutritionImport(import_id="import-test-v1", source_files=source_files,
            report={"schema_version": "food-nutrition-import-v1"}, imported_at=datetime.now(timezone.utc)))
        self.db.flush()
        self.db.add(NutritionSource(source_id="source-test-v1", import_id="import-test-v1",
            source_key="TFDA_A0550601", provider="TFDA", source_record_id="A0550601",
            source_url="https://example.invalid/tfda", source_sheet="原始來源與判定", source_row=1,
            raw_data={"來源資料ID": "A0550601", "熟製狀態": "已熟製", "烹飪方式": "電鍋煮",
                "食用前處理": None, "納入平均": "是", "熱量(kcal/100g)": 183,
                "蛋白質(g/100g)": 3.1, "脂肪(g/100g)": 0.3,
                "碳水化合物(g/100g)": 41, "糖(g/100g)": None,
                "鈉(mg/100g)": 2, "膳食纖維(g/100g)": 0.6}))
        self.db.flush()
        self.db.add(FoodNutrition(nutrition_id="nutrition-test-v1", import_id="import-test-v1",
            food_id="FOOD000296", source_row=1, standard_name="白飯", cooking_state="已熟製",
            cooking_method="電鍋煮", preparation=None, calculation_method="TFDA raw row",
            energy_kcal=183, protein_g=3.1, fat_g=0.3, carbohydrate_g=41, sugars_g=None,
            sodium_mg=2, dietary_fiber_g=0.6, nutrition_complete=False,
            quality_issues=["missing:sugars_g"], raw_data={}))
        self.db.flush()
        self.db.add(NutritionSourceLink(nutrition_id="nutrition-test-v1",
            source_id="source-test-v1", included_in_calculation=True))
        self.db.add(NutritionFoodMapping(mapping_id="mapping-test-v1", import_id="import-test-v1",
            recipe_release_id="recipe-test-v1", food_id="FOOD000296",
            nutrition_id="nutrition-test-v1", source_id="source-test-v1",
            cooking_state="已熟製", cooking_method="電鍋煮", preparation=None,
            approval_status="pending_review", mapping_version="mapping-test-v1",
            evidence={"test_only": True}))
        self.db.commit()

    def factory(self):
        db = self.db
        class ExistingSession:
            def __enter__(self):
                return db
            def __exit__(self, *_):
                return False
        return ExistingSession()

    def line(self, **kwargs):
        values = dict(food_id="FOOD000296", name="白飯", grams_per_n=Decimal("160"),
                      recipe_ingredient_id="RI000001", cooking_method="電鍋煮",
                      weight_basis="cooked", role="staple")
        values.update(kwargs)
        return IngredientLine(**values)

    def calculator(self):
        return versioned_sql_calculator(self.factory,
            recipe_release_id="recipe-test-v1", nutrition_import_id="import-test-v1",
            mapping_version="mapping-test-v1")

    def test_pending_mapping_blocks_calculation_without_fallback(self):
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_mapping_pending")
        self.assertIsNone(row.nutrients["energy_kcal"])

    def test_approved_mapping_reports_excluded_and_missing_source_link(self):
        mapping = self.db.get(NutritionFoodMapping, "mapping-test-v1")
        mapping.approval_status = "approved"
        link = self.db.get(NutritionSourceLink, (mapping.nutrition_id, mapping.source_id))
        link.included_in_calculation = False
        self.db.commit()
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_source_link_excluded")

        self.db.delete(link)
        self.db.commit()
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_source_link_missing")
        self.assertIsNone(row.nutrients["energy_kcal"])

    def test_synthetic_source_cannot_be_requested_for_formal_import(self):
        [row] = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1",
            source_provider="SYNTHETIC_TEST")([(self.line(), Decimal("160"))])
        self.assertEqual("nutrition_source_provider_not_allowed", row.error)

    def test_approved_exact_cooked_sample_scales_and_preserves_null(self):
        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-test-v1").values(approval_status="approved"))
        self.db.commit()
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertIsNone(row.error)
        self.assertEqual(row.nutrients["energy_kcal"], Decimal("292.8"))
        self.assertEqual(row.nutrients["protein_g"], Decimal("4.96"))
        self.assertIsNone(row.nutrients["sugars_g"])
        self.assertEqual(row.trace["sample_id"], "A0550601")
        self.assertEqual(row.trace["nutrition_import_id"], "import-test-v1")
        self.assertFalse(row.trace["cooking_conversion_applied"])

    def test_configured_cooked_rice_staple_uses_exact_source_method_without_conversion(self):
        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-test-v1").values(approval_status="approved"))
        self.db.commit()
        line = staples.resolve("白飯").line
        self.assertEqual(line.food_id, "FOOD000296")
        self.assertEqual(line.weight_basis, "cooked")
        self.assertEqual(line.cooking_method, "電鍋煮")
        [row] = self.calculator()([(line, Decimal("160"))])
        self.assertIsNone(row.error)
        self.assertEqual(row.trace["sample_id"], "A0550601")
        self.assertFalse(row.trace["cooking_conversion_applied"])
        self.assertEqual(row.nutrients["energy_kcal"], Decimal("292.8"))

    def test_wrong_recipe_release_blocks_even_when_mapping_is_approved(self):
        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-test-v1").values(approval_status="approved"))
        self.db.commit()
        [row] = versioned_sql_calculator(self.factory, recipe_release_id="other-release",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1")(
                [(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_version_not_found_or_inactive")

    def test_unconfigured_versions_are_a_blocker(self):
        [row] = versioned_sql_calculator(self.factory, recipe_release_id=None,
            nutrition_import_id=None, mapping_version=None)([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_version_not_configured")

    def test_wrong_import_id_food_id_cooking_method_and_sample_are_explicit_blockers(self):
        cases = [
            ("other-import", self.line(), "nutrition_version_not_found_or_inactive"),
            ("import-test-v1", self.line(food_id="FOOD000039"), "nutrition_observation_not_found"),
            ("import-test-v1", self.line(cooking_method="水煮"), "nutrition_observation_not_found"),
        ]
        for import_id, line, expected in cases:
            with self.subTest(import_id=import_id, food_id=line.food_id, method=line.cooking_method):
                [row] = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
                    nutrition_import_id=import_id, mapping_version="mapping-test-v1")(
                        [(line, Decimal("160"))])
                self.assertEqual(row.error, expected)

        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-test-v1").values(approval_status="approved"))
        self.db.execute(update(NutritionSource).where(
            NutritionSource.source_id == "source-test-v1").values(source_record_id="A0550601_WRONG"))
        self.db.commit()
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_mapping_source_mismatch")

    def test_two_approved_source_variants_are_ambiguous(self):
        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-test-v1").values(approval_status="approved"))
        self.db.add(NutritionSource(source_id="source-second-v1", import_id="import-test-v1",
            source_key="TFDA_A0550601_SECOND", provider="TFDA", source_record_id="A0550601",
            source_url="https://example.invalid/tfda", source_sheet="原始來源與判定", source_row=2,
            raw_data={"來源資料ID": "A0550601", "熟製狀態": "已熟製", "烹飪方式": "電鍋煮",
                "食用前處理": None, "納入平均": "是", "熱量(kcal/100g)": 183,
                "蛋白質(g/100g)": 3.1, "脂肪(g/100g)": 0.3,
                "碳水化合物(g/100g)": 41, "糖(g/100g)": None,
                "鈉(mg/100g)": 2, "膳食纖維(g/100g)": 0.6}))
        self.db.flush()
        self.db.add(FoodNutrition(nutrition_id="nutrition-second-v1", import_id="import-test-v1",
            food_id="FOOD000296", source_row=2, standard_name="白飯", cooking_state="已熟製",
            cooking_method="電鍋煮", preparation=None, calculation_method="TFDA raw row",
            energy_kcal=183, protein_g=3.1, fat_g=0.3, carbohydrate_g=41, sugars_g=None,
            sodium_mg=2, dietary_fiber_g=0.6, nutrition_complete=False,
            quality_issues=["missing:sugars_g"], raw_data={}))
        self.db.flush()
        self.db.add(NutritionSourceLink(nutrition_id="nutrition-second-v1",
            source_id="source-second-v1", included_in_calculation=True))
        self.db.flush()
        self.db.add(NutritionFoodMapping(mapping_id="mapping-second-v1", import_id="import-test-v1",
            recipe_release_id="recipe-test-v1", food_id="FOOD000296",
            nutrition_id="nutrition-second-v1", source_id="source-second-v1",
            cooking_state="已熟製", cooking_method="電鍋煮", preparation=None,
            approval_status="approved", mapping_version="mapping-test-v1",
            evidence={"test_only": True}))
        self.db.commit()
        [row] = self.calculator()([(self.line(), Decimal("160"))])
        self.assertEqual(row.error, "nutrition_mapping_ambiguous")

    def test_mapping_uniqueness_is_scoped_to_recipe_release(self):
        self.db.add(RecipeDataRelease(release_id="recipe-test-v2", dataset_version="test-v2",
            source_sha256="d" * 64, schema_version="test", source_kind="sql_seed",
            row_counts={}, active=False))
        self.db.flush()
        self.db.add(NutritionFoodMapping(mapping_id="mapping-test-v2", import_id="import-test-v1",
            recipe_release_id="recipe-test-v2", food_id="FOOD000296",
            nutrition_id="nutrition-test-v1", source_id="source-test-v1",
            cooking_state="已熟製", cooking_method="電鍋煮", preparation=None,
            approval_status="pending_review", mapping_version="mapping-test-v1",
            evidence={"test_only": True}))
        self.db.commit()
        count = self.db.query(NutritionFoodMapping).filter(
            NutritionFoodMapping.mapping_version == "mapping-test-v1").count()
        self.assertEqual(count, 2)

    def add_raw_observation(self, food_id="FOOD000296"):
        self.db.add(NutritionSource(source_id="source-raw-v1", import_id="import-test-v1",
            source_key="TFDA_RAW001", provider="TFDA", source_record_id="RAW001",
            source_url="https://example.invalid/tfda", source_sheet="原始來源與判定", source_row=2,
            raw_data={"來源資料ID": "RAW001", "熟製狀態": "未熟製", "烹飪方式": None,
                "食用前處理": "需烹調", "納入平均": "是", "熱量(kcal/100g)": 180,
                "蛋白質(g/100g)": 3, "脂肪(g/100g)": 0.2,
                "碳水化合物(g/100g)": 40, "糖(g/100g)": None,
                "鈉(mg/100g)": 1, "膳食纖維(g/100g)": 0.5}))
        self.db.flush()
        self.db.add(FoodNutrition(nutrition_id="nutrition-raw-v1", import_id="import-test-v1",
            food_id=food_id, source_row=2, standard_name="合成生食材", cooking_state="未熟製",
            cooking_method=None, preparation="需烹調", calculation_method="TFDA raw row",
            energy_kcal=180, protein_g=3, fat_g=0.2, carbohydrate_g=40, sugars_g=None,
            sodium_mg=1, dietary_fiber_g=0.5, nutrition_complete=False,
            quality_issues=["missing:sugars_g"], raw_data={}))
        self.db.flush()
        self.db.add(NutritionSourceLink(nutrition_id="nutrition-raw-v1",
            source_id="source-raw-v1", included_in_calculation=True))
        self.db.add(NutritionFoodMapping(mapping_id="mapping-raw-v1", import_id="import-test-v1",
            recipe_release_id="recipe-test-v1", food_id=food_id,
            nutrition_id="nutrition-raw-v1", source_id="source-raw-v1",
            cooking_state="未熟製", cooking_method=None, preparation="需烹調",
            approval_status="approved", mapping_version="mapping-test-v1",
            evidence={"test_only": True}))
        self.db.commit()

    def test_raw_to_cooked_needs_t04_receipt(self):
        self.add_raw_observation()
        [row] = self.calculator()([(self.line(weight_basis="raw", cooking_method="炒"), Decimal("160"))])
        self.assertEqual(row.error, "t04_cooking_receipt_required")
        self.db.execute(update(NutritionFoodMapping).where(
            NutritionFoodMapping.mapping_id == "mapping-raw-v1").values(cooking_state="未熟製原料"))
        self.db.execute(update(FoodNutrition).where(
            FoodNutrition.nutrition_id == "nutrition-raw-v1").values(cooking_state="未熟製原料"))
        self.db.execute(update(NutritionSource).where(
            NutritionSource.source_id == "source-raw-v1").values(raw_data={
                "來源資料ID": "RAW001", "熟製狀態": "未熟製原料", "烹飪方式": None,
                "食用前處理": "需烹調", "納入平均": "是", "熱量(kcal/100g)": 180,
                "蛋白質(g/100g)": 3, "脂肪(g/100g)": 0.2,
                "碳水化合物(g/100g)": 40, "糖(g/100g)": None,
                "鈉(mg/100g)": 1, "膳食纖維(g/100g)": 0.5}))
        self.db.commit()
        [row] = self.calculator()([(self.line(food_id="FOOD000296", weight_basis="raw", cooking_method="炒"),
                                    Decimal("160"))])
        self.assertEqual(row.error, "t04_cooking_receipt_required")

    def test_raw_to_cooked_uses_receipt_parameters_and_not_t04_totals(self):
        # Reuse the raw observation above with an approved exact mapping.
        self.add_raw_observation()
        line = self.line(weight_basis="raw", cooking_method="炒")
        receipt = {"recipe_release_id": "recipe-test-v1", "nutrition_import_id": "import-test-v1",
            "mapping_version": "mapping-test-v1", "recipe_ingredient_id": "RI000001",
            "food_id": line.food_id, "sample_id": "RAW001",
            "nutrition_id": "nutrition-raw-v1", "nutrition_source_id": "source-raw-v1",
            "cooking_state": "未熟製", "cooking_method": "炒", "weight_basis": "raw",
            "weight_g": "160", "source_version": "T04-test-v1", "is_estimated": True,
            "reference_ids": ["usda-test-1"], "assumptions": ["test assumption"],
            "parameters": {"sample_id": "RAW001", "cooking_method": "炒", "yield_factor": "0.75",
                            "retention": {"energy_kcal": "0.9", "protein_g": "0.8",
                                          "fat_g": "0.8", "carbohydrate_g": "0.8"}},
            # Must never be consumed by T12.
            "nutrition": {"energy_kcal": 999999, "protein_g": 999999}}
        [row] = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1",
            cooking_receipts={"RI000001": receipt})([(line, Decimal("160"))])
        self.assertIsNone(row.error)
        self.assertEqual(row.nutrients["energy_kcal"], Decimal("259.2"))
        self.assertEqual(row.nutrients["protein_g"], Decimal("3.84"))
        self.assertIsNone(row.nutrients["sugars_g"])
        self.assertTrue(row.trace["cooking_conversion_applied"])
        self.assertEqual(row.trace["cooked_weight_g"], Decimal("120.00"))
        self.assertTrue(row.trace["is_estimated"])
        self.assertEqual(row.trace["cooking_receipt"]["source_version"], "T04-test-v1")

    def test_persisted_receipt_is_loaded_by_formal_sql_calculator(self):
        self.add_raw_observation()
        line = self.line(weight_basis="raw", cooking_method="炒")
        receipt_id = __import__("app.services.t04_receipts", fromlist=["persist_receipt"]).persist_receipt(
            self.factory, recipe_release_id="recipe-test-v1", nutrition_import_id="import-test-v1",
            mapping_version="mapping-test-v1", recipe_ingredient_id="RI000001", food_id=line.food_id,
            nutrition_id="nutrition-raw-v1", nutrition_source_id="source-raw-v1", cooking_method="炒",
            weight_basis="raw", weight_g="160",
            parameters={"yield_factor":"0.75","sample_id":"RAW001",
                        "retention":{"energy_kcal":"0.9","protein_g":"0.8"}},
            source_version="T04-test-v1", is_estimated=True,
            assumptions=["synthetic test assumption"], reference_ids=["test-reference"])
        self.assertTrue(receipt_id.startswith("t04-"))
        self.assertEqual(self.db.query(T04CookingReceipt).filter_by(recipe_ingredient_id="RI000001").count(), 1)
        [row] = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1")(
                [(line, Decimal("160"))])
        self.assertIsNone(row.error)
        self.assertEqual(row.nutrients["energy_kcal"], Decimal("259.2"))
        self.assertEqual(row.nutrients["protein_g"], Decimal("3.84"))

    def test_receipt_bound_to_wrong_sample_or_weight_is_rejected(self):
        self.add_raw_observation()
        line = self.line(weight_basis="raw", cooking_method="炒")
        base = {"recipe_release_id": "recipe-test-v1", "nutrition_import_id": "import-test-v1",
            "mapping_version": "mapping-test-v1", "food_id": line.food_id, "sample_id": "RAW001",
            "nutrition_id": "nutrition-raw-v1", "nutrition_source_id": "source-raw-v1",
            "cooking_state": "未熟製", "cooking_method": "炒", "weight_basis": "raw",
            "weight_g": "160", "source_version": "T04-test-v1", "is_estimated": True,
            "reference_ids": ["usda-test-1"], "assumptions": ["test assumption"],
            "parameters": {"sample_id": "RAW001", "cooking_method": "炒", "yield_factor": "0.75",
                            "retention": {"energy_kcal": "0.9", "protein_g": "0.8"}}}
        for changes in ({"sample_id": "OTHER"}, {"weight_g": "161"},
                        {"recipe_ingredient_id": "RI000002"},
                        {"nutrition_import_id": "other-import"}, {"parameters": {"yield_factor": "-1"}}):
            with self.subTest(changes=changes):
                receipt = {**base, **changes}
                [row] = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
                    nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1",
                    cooking_receipts={"RI000001": receipt})([(line, Decimal("160"))])
                self.assertIn(row.error, {"t04_cooking_receipt_mismatch", "t04_cooking_receipt_invalid",
                                          "nutrition_observation_not_found", "nutrition_mapping_pending"})

    def test_full_meal_calculation_blocks_or_uses_exact_t04_receipt(self):
        self.db.add(IngredientAlias(food_id="FOOD000039", recipe_release_id="recipe-test-v1",
            canonical_name="合成食材", aliases=[], ambiguous_aliases=[], review_status="approved", quality_issues=[]))
        self.db.add(IngredientAlias(food_id="FOOD000040", recipe_release_id="recipe-test-v1",
            canonical_name="合成油", aliases=[], ambiguous_aliases=[], review_status="approved", quality_issues=[]))
        self.db.add(IngredientAlias(food_id="FOOD000045", recipe_release_id="recipe-test-v1",
            canonical_name="合成調味", aliases=[], ambiguous_aliases=[], review_status="approved", quality_issues=[]))
        self.db.flush()
        self.add_raw_observation("FOOD000039")
        # Synthetic-only test data. The menu includes staple, oil and seasoning
        # lines so each is evaluated through the same SQL calculator snapshot.
        # Add a cooked rice sample with exact SQL mapping.
        self.db.add(NutritionSource(source_id="rice-source", import_id="import-test-v1",
            source_key="TFDA_RICE", provider="TFDA", source_record_id="A0550601",
            source_url="https://example.invalid/rice", source_sheet="原始來源與判定", source_row=3,
            raw_data={"來源資料ID":"A0550601","熟製狀態":"已熟製","烹飪方式":"電鍋煮",
                "食用前處理":None,"納入平均":"是","熱量(kcal/100g)":180,
                "蛋白質(g/100g)":3,"脂肪(g/100g)":1,"碳水化合物(g/100g)":40,
                "糖(g/100g)":0,"鈉(mg/100g)":1,"膳食纖維(g/100g)":1}))
        self.db.flush()
        self.db.add(FoodNutrition(nutrition_id="rice-nutrition", import_id="import-test-v1",
            food_id="FOOD000296", source_row=3, standard_name="白飯", cooking_state="已熟製",
            cooking_method="電鍋煮", preparation=None, calculation_method="synthetic-test",
            energy_kcal=180, protein_g=3, fat_g=1, carbohydrate_g=40, sugars_g=0,
            sodium_mg=1, dietary_fiber_g=1, nutrition_complete=True, quality_issues=[], raw_data={}))
        self.db.flush()
        self.db.add(NutritionSourceLink(nutrition_id="rice-nutrition", source_id="rice-source",
            included_in_calculation=True))
        self.db.add(NutritionFoodMapping(mapping_id="rice-mapping", import_id="import-test-v1",
            recipe_release_id="recipe-test-v1", food_id="FOOD000296", nutrition_id="rice-nutrition",
            source_id="rice-source", cooking_state="已熟製", cooking_method="電鍋煮", preparation=None,
            approval_status="approved", mapping_version="mapping-test-v1", evidence={"test_only":True}))
        for fid, name, suffix in (("FOOD000040", "合成油", "oil"), ("FOOD000045", "合成調味", "season")):
            sid, nid, sample = f"{suffix}-source", f"{suffix}-nutrition", f"{suffix.upper()}001"
            raw = {"來源資料ID":sample,"熟製狀態":"未熟製","烹飪方式":None,
                "食用前處理":None,"納入平均":"是","熱量(kcal/100g)":100,
                "蛋白質(g/100g)":0,"脂肪(g/100g)":10,"碳水化合物(g/100g)":0,
                "糖(g/100g)":0,"鈉(mg/100g)":1,"膳食纖維(g/100g)":0}
            self.db.add(NutritionSource(source_id=sid, import_id="import-test-v1", source_key=sid,
                provider="TFDA", source_record_id=sample, source_url="https://example.invalid/synthetic",
                source_sheet="test", source_row=4 if suffix == "oil" else 5, raw_data=raw))
            self.db.flush()
            self.db.add(FoodNutrition(nutrition_id=nid, import_id="import-test-v1", food_id=fid,
                source_row=4 if suffix == "oil" else 5, standard_name=name, cooking_state="未熟製",
                cooking_method=None, preparation=None, calculation_method="synthetic-test",
                energy_kcal=100, protein_g=0, fat_g=10, carbohydrate_g=0, sugars_g=0,
                sodium_mg=1, dietary_fiber_g=0, nutrition_complete=True, quality_issues=[], raw_data={}))
            self.db.flush()
            self.db.add(NutritionSourceLink(nutrition_id=nid, source_id=sid, included_in_calculation=True))
            self.db.add(NutritionFoodMapping(mapping_id=f"{suffix}-mapping", import_id="import-test-v1",
                recipe_release_id="recipe-test-v1", food_id=fid, nutrition_id=nid, source_id=sid,
                cooking_state="未熟製", cooking_method=None, preparation=None, approval_status="approved",
                mapping_version="mapping-test-v1", evidence={"test_only":True}))
        self.db.commit()
        raw_line = self.line(food_id="FOOD000039", name="合成食材", recipe_ingredient_id="RI-RAW",
                             grams_per_n=Decimal("100"), weight_basis="raw", cooking_method="炒", role="dish")
        rice = self.line(food_id="FOOD000296", name="白飯", recipe_ingredient_id="RI-RICE",
                         grams_per_n=Decimal("100"), weight_basis="cooked", cooking_method="電鍋煮", role="staple")
        oil = self.line(food_id="FOOD000040", name="合成油", recipe_ingredient_id="RI-OIL",
                        grams_per_n=Decimal("10"), weight_basis="raw", consumed_as_is=True, role="dish")
        seasoning = self.line(food_id="FOOD000045", name="合成調味", recipe_ingredient_id="RI-SEASON",
                              grams_per_n=Decimal("5"), weight_basis="raw", consumed_as_is=True, role="dish")
        lines = [raw_line, rice, oil, seasoning]
        targets = MealTargets(Fraction(4), Fraction(1500), Fraction(24))
        no_receipt = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1")
        blocked = calculate_meal(lines, targets, mode="database", calculator=no_receipt)
        self.assertFalse(blocked["adoptable"])
        self.assertTrue(any(b["code"] == "NUTRITION_INCOMPLETE" for b in blocked["block_reasons"]))
        receipt = {"recipe_release_id":"recipe-test-v1","nutrition_import_id":"import-test-v1",
            "mapping_version":"mapping-test-v1","recipe_ingredient_id":"RI-RAW",
            "food_id":"FOOD000039","sample_id":"RAW001",
            "nutrition_id":"nutrition-raw-v1","nutrition_source_id":"source-raw-v1",
            "cooking_state":"未熟製","cooking_method":"炒","weight_basis":"raw","weight_g":"100",
            "source_version":"T04-test-v1","is_estimated":True,"reference_ids":["ref"],
            "assumptions":["synthetic"],"parameters":{"sample_id":"RAW001","cooking_method":"炒",
                "yield_factor":"1","retention":{"energy_kcal":"1","protein_g":"1","fat_g":"1",
                    "carbohydrate_g":"1","sodium_mg":"1","dietary_fiber_g":"1"}}}
        approved = versioned_sql_calculator(self.factory, recipe_release_id="recipe-test-v1",
            nutrition_import_id="import-test-v1", mapping_version="mapping-test-v1",
            cooking_receipts={"RI-RAW":receipt})
        result = calculate_meal(lines, targets, mode="database", calculator=approved)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual({x["food_id"] for x in result["ingredients"]},
                         {"FOOD000039", "FOOD000040", "FOOD000045", "FOOD000296"})
        self.assertEqual({x.food_id for x in result["requirements"]},
                         {"FOOD000039", "FOOD000040", "FOOD000045"})

if __name__ == "__main__":
    unittest.main()
