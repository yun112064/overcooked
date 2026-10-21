import json
from pathlib import Path
import unittest

import nutrition_service as service
from llm_cooking_estimator import NUTRIENTS

BASE = Path(__file__).parent
SOURCE = BASE / 'data' / 'source' / '食材營養成分表.xlsx'


class WorkbookSourceTests(unittest.TestCase):
    def test_packaged_workbook_and_derived_tables_have_matching_counts(self):
        self.assertTrue(SOURCE.exists())
        manifest = json.loads((BASE / 'data' / 'workbook_manifest.json').read_text(encoding='utf-8'))
        foods = json.loads((BASE / 'data' / 'workbook_foods.json').read_text(encoding='utf-8'))
        ids = json.loads((BASE / 'data' / 'ingredient_id_master.json').read_text(encoding='utf-8'))
        tfda = [r for r in foods if r['資料來源'] == 'TFDA']
        usda = [r for r in foods if r['資料來源'] == 'USDA']
        self.assertEqual((len(tfda), len(usda), len(ids)), (2213, 372, 461))
        self.assertEqual(manifest['row_counts'], {'TFDA': 2213, 'USDA': 372, 'ingredient_ids': 461})
        self.assertEqual(len(service.ingredients), len(tfda))
        self.assertEqual(len({r['來源食品ID'] for r in foods}), len(foods))

    def test_chicken_nutrition_comes_from_workbook_with_sugar(self):
        record = next(r for r in service.ingredients if r['ingredient_id'] == 'I0402402')
        self.assertEqual(record['source_food_id'], 'TFDA_I0402402')
        self.assertEqual(record['source']['source_file'], '食材營養成分表.xlsx')
        self.assertEqual(record['nutrition_per_100g']['sugars_g'], 0)
        self.assertEqual(service.calculate_nutrition('雞胸肉', 150)['nutrition']['sugars_g'], 0)
        self.assertEqual(len(NUTRIENTS), 7)

    def test_rice_sugar_missing_and_cooked_sesame_complete(self):
        rice = service.calculate_recipe_nutrition('飯', [
            {'name': '白飯', 'weight_g': 150, 'weight_basis': 'cooked'}])
        self.assertIsNone(rice['total_nutrition']['sugars_g'])
        self.assertFalse(rice['nutrition_complete'])
        sesame = service.calculate_recipe_nutrition('芝麻', [
            {'name': '白芝麻(熟)', 'weight_g': 100, 'weight_basis': 'cooked'}])
        self.assertEqual(sesame['total_nutrition']['sugars_g'], 0.5)
        self.assertTrue(sesame['nutrition_complete'])

    def test_generated_tfda_nutrients_match_all_workbook_rows(self):
        foods = json.loads((BASE / 'data' / 'workbook_foods.json').read_text(encoding='utf-8'))
        records = {r['source_food_id']: r for r in service.ingredients}
        columns = {'熱量(kcal/100g)': 'energy_kcal', '蛋白質(g/100g)': 'protein_g',
                   '脂肪(g/100g)': 'fat_g', '碳水化合物(g/100g)': 'carbohydrate_g',
                   '糖(g/100g)': 'sugars_g', '鈉(mg/100g)': 'sodium_mg',
                   '膳食纖維(g/100g)': 'dietary_fiber_g'}
        for food in foods:
            if food['資料來源'] != 'TFDA':
                continue
            got = records[food['來源食品ID']]
            for column, key in columns.items():
                self.assertEqual(got['nutrition_per_100g'][key], food[column], (food['來源食品ID'], key))


if __name__ == '__main__':
    unittest.main()
