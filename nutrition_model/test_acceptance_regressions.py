"""Acceptance regressions: weight semantics, input safety and clean Python CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import nutrition_service as service
from llm_cooking_estimator import NUTRIENTS, finite_number


def retention_only(_):
    return {'yield_factor': None, 'reference_ids': [],
            'nutrient_retention_factors': {key: 1 for key in NUTRIENTS},
            'assumptions': ['Synthetic acceptance fixture; not a nutrition recommendation.'],
            'missing_information': []}


class AcceptanceRegressions(unittest.TestCase):
    def test_cooked_food_with_method_requires_cooked_weight_basis(self):
        result = service.calculate_recipe_nutrition('weight basis', [{
            'name': '白芝麻(熟)', 'weight_g': 100,
            'cooking_method': '烤', 'cooked_weight_g': 80}])
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_ingredients'][0]['error'],
                         'cooked_food_requires_cooked_weight_basis')

    def test_dish_measured_weight_completes_retention_only_recipe(self):
        result = service.calculate_recipe_nutrition('synthetic carrot soup', [{
            'name': '紅蘿蔔', 'weight_g': 100, 'cooking_method': '水煮'}],
            cooked_weight_g=200, estimator=retention_only)
        self.assertTrue(result['nutrition_complete'])
        self.assertTrue(result['cooking_adjustment_complete'])
        self.assertEqual(result['cooking_incomplete'], [])
        self.assertEqual(result['nutrition_per_100g']['energy_kcal'], 19.5)
        self.assertIsNone(result['ingredients'][0]['cooked_weight_g'])

    def test_dish_weight_does_not_fill_missing_fiber(self):
        result = service.calculate_recipe_nutrition('chicken', [{
            'name': '雞胸肉', 'weight_g': 150, 'cooking_method': '烤'}],
            cooked_weight_g=120, estimator=retention_only)
        self.assertFalse(result['cooking_adjustment_complete'])
        self.assertIsNone(result['total_nutrition']['dietary_fiber_g'])

    def test_recipe_keeps_unrounded_totals_for_boundary_checks(self):
        result = service.calculate_recipe_nutrition('rice and soy sauce', [
            {'name': '白飯', 'weight_g': 133, 'weight_basis': 'cooked'},
            {'name': '醬油', 'weight_g': 7, 'consumed_as_is': True}])
        exact = result['total_nutrition_unrounded']
        shown = result['total_nutrition']
        self.assertEqual(set(exact), set(shown))
        for key, value in exact.items():
            if value is None:
                self.assertIsNone(shown[key])
            else:
                self.assertEqual(shown[key], round(value, 2))
        # The display value is rounded; the exact sum is not.
        self.assertNotEqual(exact['protein_g'], shown['protein_g'])
        self.assertAlmostEqual(exact['protein_g'], 4.669, places=9)

    def test_unrepresentable_integer_is_rejected_without_exception(self):
        self.assertFalse(finite_number(10 ** 400, positive=True))
        result = service.calculate_nutrition('白芝麻(熟)', 10 ** 400)
        self.assertFalse(result['success'])

    def run_clean_cli(self, arguments, env_file=None):
        env = {key: val for key, val in os.environ.items()
               if not key.startswith(('GEMINI_', 'NUTRITION_LLM_'))}
        env['PYTHONUTF8'] = '1'
        script = Path(__file__).parent / 'calculate_recipe.py'
        with tempfile.TemporaryDirectory() as directory:
            if env_file is not None:
                (Path(directory) / '.env').write_text(env_file, encoding='utf-8-sig')
            return subprocess.run([sys.executable, '-S', str(script), *arguments],
                                  cwd=directory, env=env, text=True,
                                  encoding='utf-8', capture_output=True)

    def test_cli_works_without_third_party_packages(self):
        result = self.run_clean_cli(['--help'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--preview-llm', result.stdout)

    def test_clean_cli_reads_quoted_env_without_exposing_key(self):
        recipe = Path(__file__).parent / 'examples' / 'roast_chicken.json'
        result = self.run_clean_cli(['--input', str(recipe)],
            'export NUTRITION_LLM_PROVIDER="unsupported" # comment\n'
            'GEMINI_API_KEY="acceptance-fake-key"\n')
        self.assertEqual(result.returncode, 2, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['ingredients'][0]['estimation_error'], 'invalid_llm_provider')
        self.assertNotIn('acceptance-fake-key', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
