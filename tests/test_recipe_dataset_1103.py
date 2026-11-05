"""2026-11-04 換成 recipe_dataset_1103_V3：欄名改為 instruction、步驟編輯備註、營養檔失效引用、審核沿用。"""
import unittest
from pathlib import Path

from app.services.nutrition_import import parse_nutrition_workbooks
from app.services.recipe_import import STEP_EDITOR_NOTE, load_review_record, load_source_rights, parse_recipe_workbook

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "data/recipe_dataset_1103_V3.xlsx"
NUTRITION = ROOT / "knowledge_rag/sources/All_Food_ID_1018.xlsx"


class RecipeDataset1103Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = load_review_record(ROOT / "config/recipe_reviews.json")
        cls.sources = load_source_rights(ROOT / "config/recipe_sources.json")

    def test_renamed_step_column_is_read_and_editor_note_is_removed(self):
        bundle = parse_recipe_workbook(WORKBOOK, self.sources, self.review)
        self.assertGreater(len(bundle.recipe_steps), 1400)
        self.assertFalse(any(s["instruction_clean"].startswith(STEP_EDITOR_NOTE) for s in bundle.recipe_steps))

    def test_review_carries_over_and_confirmed_distinct_pairs_stay_eligible(self):
        bundle = parse_recipe_workbook(WORKBOOK, self.sources, self.review)
        self.assertTrue(bundle.report["review_record"]["applied"])
        eligible = {r["recipe_id"] for r in bundle.recipes if r["is_eligible"]}
        self.assertTrue({"R0180", "R0182", "R0197", "R0201"} <= eligible)

    def test_nutrition_citations_of_removed_or_changed_recipe_lines_are_reported_not_fatal(self):
        bundle = parse_nutrition_workbooks(WORKBOOK, WORKBOOK, NUTRITION, source_rights=self.sources,
                                           review_record=self.review)
        stale = bundle.report["reconciliation"]["removed_recipe_source_refs"]
        self.assertEqual({r["food_id"] for r in stale}, {"FOOD000061", "FOOD000441", "FOOD000472", "FOOD000496"})
        self.assertTrue(all(r["recipe_ingredient_id"].startswith("RI") for r in stale))
        self.assertEqual(bundle.report["counts"]["removed_recipe_source_refs"], len(stale))


class AnhydrousMilkFatSupplementTests(unittest.TestCase):
    def test_amf_has_reviewed_usda_values(self):
        from nutrition_planning.nutrition_supplement import load_nutrition_supplements
        amf = load_nutrition_supplements().values["FOOD001993"]
        self.assertEqual(amf.record_id, "USDA_FDC_173412")
        self.assertEqual(str(amf.values["energy_kcal"]), "876")
        self.assertEqual(str(amf.values["fat_g"]), "99.48")
