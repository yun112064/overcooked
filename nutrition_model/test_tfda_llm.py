import math
import json
import contextlib
from io import StringIO
import os
import subprocess
import sys
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import nutrition_service as service
import cooking_yield_service as yields

KEYS = ['energy_kcal', 'carbohydrate_g', 'protein_g', 'fat_g',
        'sodium_mg', 'dietary_fiber_g', 'sugars_g']


def food(identifier, state, values, **extra):
    values = [*values, 0] if len(values) == 6 else values
    return dict(ingredient_id=identifier, standard_name='測試食材' if state == 'raw' else '測試熟食',
                aliases=[], state=state, nutrition_per_100g=dict(zip(KEYS, values)),
                source={'provider': 'TFDA', 'source_id': identifier}, **extra)


class CookingFlowTests(unittest.TestCase):
    def setUp(self):
        self.raw = food('raw-test', 'raw', [100, 2, 20, 1, 10, None])
        self.cooked = food('cooked-test', 'cooked', [150, 3, 25, 2, 15, 0],
                           cooking_method='烤', raw_ingredient_id='raw-test')
        self.records = patch.object(service, 'ingredients', [self.raw, self.cooked])
        self.records.start()
        self.addCleanup(self.records.stop)

    def calculate(self, **kwargs):
        return service.calculate_ingredient_nutrition_with_cooking('測試食材', 100, '烤', **kwargs)

    def estimate(self, payload):
        return {'yield_factor': 0.75 if payload['yield_references'] else None,
                'reference_ids': [r['reference_id'] for r in payload['yield_references'][:1]],
                'nutrient_retention_factors': {k: 0.9 for k in KEYS},
                'assumptions': ['測試用估算：瀝乾後食用'], 'missing_information': []}

    def test_tfda_cooked_has_priority_and_no_llm_when_weight_known(self):
        def forbidden(_):
            self.fail('complete TFDA with measured weight must not call LLM')
        result = self.calculate(cooked_weight_g=80, estimator=forbidden)
        self.assertEqual(result['nutrition_basis'], 'cooked_tfda')
        self.assertEqual(result['nutrition']['protein_g'], 20)
        self.assertEqual(result['yield_basis'], 'measured')
        self.assertFalse(result['is_estimated'])

    def test_cooked_weight_input_is_not_yielded_again(self):
        result = service.calculate_ingredient_nutrition_with_cooking(
            '測試食材', 80, '烤', weight_basis='cooked')
        self.assertEqual(result['cooked_weight_g'], 80)
        self.assertEqual(result['nutrition']['protein_g'], 20)
        self.assertIsNone(result['raw_weight_g'])

    def test_legacy_cooked_entry_point_uses_same_flow(self):
        result = service.calculate_cooked_nutrition('測試食材', 100, '烤', cooked_weight_g=80)
        self.assertEqual(result['nutrition']['protein_g'], 20)

    def test_missing_configuration_does_not_claim_raw_as_cooked(self):
        service.ingredients.remove(self.cooked)
        with patch.dict('os.environ', {}, clear=True):
            result = self.calculate()
        self.assertFalse(result['nutrition_complete'])
        self.assertIsNone(result['nutrition']['protein_g'])
        self.assertEqual(result['raw_reference']['nutrition']['protein_g'], 20)

    def test_llm_retention_does_not_multiply_nutrients_by_yield(self):
        service.ingredients.remove(self.cooked)
        with patch.object(yields, 'food_mapping', {'測試食材': {'usda_keywords': ['chicken', 'breast']}}):
            result = self.calculate(estimator=self.estimate)
        self.assertEqual(result['nutrition']['protein_g'], 18)
        self.assertEqual(result['cooked_weight_g'], 75)
        self.assertIsNone(result['nutrition']['dietary_fiber_g'])
        self.assertTrue(result['is_estimated'])
        self.assertEqual(result['nutrition_basis'], 'raw_tfda_llm_estimate')

    def test_partial_tfda_keeps_known_values(self):
        self.cooked['nutrition_per_100g']['sodium_mg'] = None
        result = self.calculate(cooked_weight_g=80, estimator=self.estimate)
        self.assertEqual(result['nutrition']['protein_g'], 20)
        self.assertEqual(result['nutrition']['sodium_mg'], 9)
        self.assertEqual(result['nutrient_sources']['protein_g']['basis'], 'cooked_tfda')
        self.assertEqual(result['nutrient_sources']['sodium_mg']['basis'], 'raw_tfda_llm_estimate')

    def test_cooked_input_without_raw_cannot_use_raw_composition(self):
        service.ingredients.remove(self.cooked)
        result = service.calculate_ingredient_nutrition_with_cooking(
            '測試食材', 80, '烤', weight_basis='cooked', estimator=self.estimate)
        self.assertIsNone(result['nutrition']['protein_g'])

    def test_wrong_cooking_method_does_not_match_tfda(self):
        with patch.dict('os.environ', {}, clear=True):
            result = service.calculate_ingredient_nutrition_with_cooking('測試食材', 100, '水煮')
        self.assertNotEqual(result['nutrition_basis'], 'cooked_tfda')

    def test_invalid_weights_return_structured_failure(self):
        for value in [-1, 0, True, float('nan'), float('inf'), '100']:
            with self.subTest(value=value):
                result = service.calculate_ingredient_nutrition_with_cooking('測試食材', value, '烤')
                self.assertFalse(result['success'])

    def test_invalid_methods_rejected_before_llm(self):
        def forbidden(_):
            self.fail('invalid method must not call estimator')
        for method in [True, False, 0, [], ['烤'], {'method': '烤'}, '', '  ']:
            with self.subTest(method=method):
                row = service.calculate_ingredient_nutrition_with_cooking(
                    '測試食材', 100, method, cooked_weight_g=80, estimator=forbidden)
                self.assertFalse(row['success'])
                recipe = service.calculate_recipe_nutrition('錯誤方式', [
                    {'name': '測試食材', 'weight_g': 100, 'cooking_method': method}], estimator=forbidden)
                self.assertFalse(recipe['success'])

    def test_invalid_master_nutrients_remain_missing(self):
        for value in [float('nan'), float('inf'), -1, True]:
            self.raw['nutrition_per_100g']['protein_g'] = value
            result = service.calculate_nutrition('測試食材', 100)
            self.assertIsNone(result['nutrition']['protein_g'])
            self.assertFalse(result['nutrition_complete'])

    def test_recipe_servings_and_measured_weights(self):
        result = service.calculate_recipe_nutrition('測試', [
            {'name': '測試食材', 'weight_g': 100, 'cooking_method': '烤', 'cooked_weight_g': 80},
            {'name': '測試食材', 'weight_g': 100, 'cooking_method': '烤', 'cooked_weight_g': 80}], servings=2)
        self.assertEqual(result['total_nutrition']['protein_g'], 40)
        self.assertEqual(result['per_serving_nutrition']['protein_g'], 20)
        self.assertEqual(result['nutrition_per_100g']['protein_g'], 25)

    def test_no_cooking_method_keeps_raw_out_of_cooked_totals(self):
        result = service.calculate_recipe_nutrition('缺料理方式', [
            {'name': '測試食材', 'weight_g': 100}], cooked_weight_g=80)
        self.assertIsNone(result['total_nutrition']['protein_g'])
        self.assertIsNone(result['per_serving_nutrition']['protein_g'])
        self.assertIsNone(result['nutrition_per_100g']['protein_g'])
        self.assertFalse(result['nutrition_complete'])
        self.assertEqual(result['ingredients'][0]['raw_reference']['nutrition']['protein_g'], 20)

    def test_llm_yield_does_not_replace_tfda_composition(self):
        with patch.object(yields, 'food_mapping', {'測試食材': {'usda_keywords': ['chicken', 'breast']}}):
            result = self.calculate(estimator=self.estimate)
        self.assertEqual(result['yield_basis'], 'llm_estimated')
        self.assertEqual(result['nutrition_basis'], 'cooked_tfda')
        self.assertEqual(result['nutrition']['protein_g'], 18.75)

    def test_unique_exact_usda_yield_needs_no_llm_for_tfda(self):
        reference = {'yield_description': 'chicken breast meat only', 'preparation_method': 'Roasted',
                     'cooking_yield_pct': 80, 'source': 'test USDA record'}
        def forbidden(_):
            self.fail('exact yield with full TFDA must not call LLM')
        with patch.object(yields, 'food_mapping', {'測試食材': {
                'food_form': 'meat_only', 'usda_keywords': ['chicken', 'breast', 'meat only']}}):
            with patch.object(yields, 'cooking_yields', [reference]):
                result = self.calculate(estimator=forbidden)
        self.assertEqual(result['yield_basis'], 'usda_exact')
        self.assertEqual(result['nutrition']['protein_g'], 20)

    def test_incomplete_cooking_does_not_enter_recipe_total(self):
        service.ingredients.remove(self.cooked)
        with patch.dict('os.environ', {}, clear=True):
            result = service.calculate_recipe_nutrition('測試', [
                {'name': '測試食材', 'weight_g': 100, 'cooking_method': '烤'}])
        self.assertIsNone(result['total_nutrition']['protein_g'])
        self.assertFalse(result['cooking_adjustment_complete'])

    def test_empty_recipe_is_not_complete(self):
        self.assertFalse(service.calculate_recipe_nutrition('空', [])['success'])


class EstimatorTests(unittest.TestCase):
    def test_gemini_adapter_sends_usda_context_and_reads_json(self):
        import llm_cooking_estimator as llm
        from io import BytesIO
        payload = {'yield_references': [{'reference_id': 'usda-1', 'cooking_yield_pct': 72}]}
        estimate = {'yield_factor': 0.72, 'reference_ids': ['usda-1'],
                    'nutrient_retention_factors': {'protein_g': 0.9},
                    'assumptions': ['USDA 類比'], 'missing_information': []}
        body = {'candidates': [{'content': {'parts': [{'text': json.dumps(estimate)}]}}]}
        with patch.dict('os.environ', {'NUTRITION_LLM_PROVIDER': 'gemini',
                                     'GEMINI_API_KEY': 'local-test-key', 'GEMINI_MODEL': 'gemini-test-flash'}, clear=True):
            with patch.object(llm, 'urlopen', return_value=BytesIO(json.dumps(body).encode())) as transport:
                with contextlib.redirect_stdout(StringIO()) as captured:
                    result = llm.estimate_cooking(payload)
        self.assertEqual(captured.getvalue(), '')
        self.assertTrue(result['success'])
        self.assertEqual(result['yield_factor'], 0.72)
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url,
            'https://generativelanguage.googleapis.com/v1beta/models/gemini-test-flash:generateContent')
        self.assertEqual(request.get_header('X-goog-api-key'), 'local-test-key')
        sent = json.loads(request.data)
        self.assertEqual(json.loads(sent['contents'][0]['parts'][0]['text']), payload)
        self.assertEqual(sent['generationConfig']['responseMimeType'], 'application/json')

    def test_gemini_without_key_returns_configuration_error(self):
        import llm_cooking_estimator as llm
        with patch.dict('os.environ', {'NUTRITION_LLM_PROVIDER': 'gemini'}, clear=True):
            with patch.object(llm, 'urlopen') as transport:
                result = llm.estimate_cooking({'yield_references': []})
        self.assertEqual(result, {'success': False, 'error': 'llm_not_configured'})
        transport.assert_not_called()

    def test_http_adapter_sends_references_and_reads_json(self):
        import llm_cooking_estimator as llm
        from io import BytesIO
        payload = {'yield_references': [{'reference_id': 'valid'}]}
        estimate = {'yield_factor': 0.75, 'reference_ids': ['valid'],
                    'nutrient_retention_factors': {'protein_g': 0.9},
                    'assumptions': ['test'], 'missing_information': []}
        response = BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(estimate)}}]}).encode())
        with patch.dict('os.environ', {'NUTRITION_LLM_ENDPOINT': 'https://provider.example/chat/completions',
                                     'NUTRITION_LLM_MODEL': 'chosen-model'}, clear=True):
            with patch.object(llm, 'urlopen', return_value=response) as transport:
                captured = StringIO()
                with contextlib.redirect_stdout(captured):
                    result = llm.estimate_cooking(payload)
        self.assertEqual(captured.getvalue(), '')
        self.assertEqual(result['yield_factor'], 0.75)
        sent = json.loads(transport.call_args.args[0].data)
        self.assertEqual(json.loads(sent['messages'][1]['content']), payload)

    def test_retrieval_only_returns_relevant_meat(self):
        self.assertTrue(hasattr(yields, 'get_yield_references'), 'retrieval API missing')
        refs = yields.get_yield_references('雞胸肉', '烤')
        self.assertTrue(refs)
        self.assertTrue(all('chicken' in r['yield_description'].lower() for r in refs))
        self.assertEqual(yields.get_yield_references('紅蘿蔔', '水煮'), [])

    def test_validator_rejects_unknown_reference_and_invalid_numbers(self):
        import llm_cooking_estimator as llm
        payload = {'yield_references': [{'reference_id': 'valid'}]}
        valid = {'yield_factor': 0.75, 'reference_ids': ['valid'],
                 'nutrient_retention_factors': {'protein_g': 0.9},
                 'assumptions': ['test'], 'missing_information': []}
        self.assertTrue(llm.validate_estimate(valid, payload)['success'])
        for changes in [dict(reference_ids=['invented']), dict(yield_factor=math.nan),
                        dict(yield_factor=-1), dict(yield_factor=True),
                        dict(nutrient_retention_factors={'protein_g': 4}), dict(assumptions=[])]:
            result = llm.validate_estimate({**valid, **changes}, payload)
            self.assertFalse(result['success'], changes)

    def test_no_references_cannot_claim_usda_yield(self):
        import llm_cooking_estimator as llm
        result = llm.estimate_cooking({'yield_references': []}, estimator=lambda _: {
            'yield_factor': 0.8, 'reference_ids': [], 'nutrient_retention_factors': {},
            'assumptions': ['test'], 'missing_information': []})
        self.assertFalse(result['success'])

    def test_provider_error_is_structured(self):
        import llm_cooking_estimator as llm
        def broken(_):
            raise TimeoutError('provider secret must not appear')
        result = llm.estimate_cooking({'yield_references': []}, estimator=broken)
        self.assertFalse(result['success'])
        self.assertNotIn('secret', str(result))


class AdditionalFlowTests(unittest.TestCase):
    def test_cli_preview_shows_exact_usda_context_without_api_call(self):
        path = Path(__file__).parent / 'calculate_recipe.py'
        example = Path(__file__).parent / 'examples' / 'roast_chicken.json'
        process = subprocess.run([sys.executable, str(path), '--input', str(example), '--preview-llm'],
            text=True, encoding='utf-8', capture_output=True,
            env={**os.environ, 'PYTHONUTF8': '1',
                 'NUTRITION_LLM_ENDPOINT': 'https://must-not-be-called.example/chat/completions',
                 'NUTRITION_LLM_MODEL': 'preview-test'})
        self.assertEqual(process.returncode, 0, process.stderr)
        preview = json.loads(process.stdout)
        self.assertEqual(preview['request_count'], 1)
        payload = preview['llm_requests'][0]
        self.assertEqual(payload['raw_source']['source_file'], '食材營養成分表.xlsx')
        self.assertEqual(payload['raw_weight_g'], 150)
        self.assertTrue(payload['yield_references'])
        self.assertTrue(all('reference_id' in item and 'cooking_yield_pct' in item
                            for item in payload['yield_references']))

    def test_recipe_cli_preserves_missing_sugar_in_cooked_rice(self):
        path = Path(__file__).parent / 'calculate_recipe.py'
        self.assertTrue(path.exists(), 'recipe CLI missing')
        process = subprocess.run([sys.executable, str(path), '--input', '-'],
            input=json.dumps({'recipe_name': '飯', 'servings': 2, 'ingredients': [
                {'name': '白飯', 'weight_g': 300, 'weight_basis': 'cooked'}]}),
            text=True, encoding='utf-8', capture_output=True, env={**os.environ, 'PYTHONUTF8': '1'})
        self.assertEqual(process.returncode, 2, process.stderr)
        result = json.loads(process.stdout)
        self.assertFalse(result['nutrition_complete'])
        self.assertIsNone(result['total_nutrition']['sugars_g'])
        self.assertEqual(result['cooked_weight_g'], 300)

    def test_recipe_cli_complete_tfda_sesame(self):
        path = Path(__file__).parent / 'calculate_recipe.py'
        process = subprocess.run([sys.executable, str(path), '--input', '-'],
            input=json.dumps({'recipe_name': '芝麻', 'ingredients': [
                {'name': '白芝麻(熟)', 'weight_g': 100, 'weight_basis': 'cooked'}]}),
            text=True, encoding='utf-8', capture_output=True, env={**os.environ, 'PYTHONUTF8': '1'})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['total_nutrition']['sugars_g'], 0.5)

    def test_real_tfda_rice_cooked_weight_without_cooking(self):
        result = service.calculate_recipe_nutrition('白飯', [
            {'name': '白飯', 'weight_g': 150, 'weight_basis': 'cooked'}])
        reference = service.calculate_nutrition('白飯', 150)
        self.assertEqual(result['total_nutrition'], reference['nutrition'])
        self.assertFalse(result['cooking_adjustment_complete'])

    def test_oil_counted_once_with_explicit_consumed_fraction(self):
        oil = food('oil', 'raw', [900, 0, 0, 100, 0, 0])
        oil['standard_name'] = '測試油'
        with patch.object(service, 'ingredients', [oil]):
            result = service.calculate_recipe_nutrition('油', [
                {'name': '測試油', 'weight_g': 10, 'consumed_as_is': True, 'retained_fraction': 0.5}])
        self.assertEqual(result['total_nutrition']['fat_g'], 5)
        self.assertEqual(result['cooked_weight_g'], 5)
        self.assertTrue(result['cooking_adjustment_complete'])

    def test_raw_nutrition_does_not_round_before_recipe_summing(self):
        raw = food('raw', 'raw', [1.004, 0, 0, 0, 0, 0])
        with patch.object(service, 'ingredients', [raw]):
            result = service.calculate_recipe_nutrition('小數', [
                {'name': '測試食材', 'weight_g': 100, 'consumed_as_is': True},
                {'name': '測試食材', 'weight_g': 100, 'consumed_as_is': True}])
        self.assertEqual(result['total_nutrition']['energy_kcal'], 2.01)

    def test_conflicting_cooked_weights_rejected(self):
        result = service.calculate_ingredient_nutrition_with_cooking(
            '白飯', 100, '蒸煮', weight_basis='cooked', cooked_weight_g=150)
        self.assertFalse(result['success'])

    def test_tfda_metadata_is_available_to_importer(self):
        import cooked_nutrition_service as cooked_service
        self.assertTrue(hasattr(cooked_service, 'cooked_metadata_for'))
        self.assertEqual(cooked_service.cooked_metadata_for('A0550601')['cooking_method'], '蒸煮')


@unittest.skipUnless(importlib.util.find_spec('pandas'), 'optional TFDA importer requires pandas')
class ImportValidationTests(unittest.TestCase):
    def test_nonfinite_negative_and_unknown_numbers_are_missing(self):
        from import_tfda import parse_number
        for value in ['nan', 'NaN', 'inf', 'Infinity', '-3', None, 'ND', '--']:
            self.assertIsNone(parse_number(value), value)
        self.assertEqual(parse_number('0'), 0)
        self.assertEqual(parse_number('1,234.5'), 1234.5)

    def test_missing_or_mismatched_units_are_rejected(self):
        from import_tfda import unit_matches
        for unit in [None, float('nan'), '', 'g']:
            self.assertFalse(unit_matches('sodium_mg', unit))
        self.assertTrue(unit_matches('sodium_mg', 'mg'))


if __name__ == '__main__':
    unittest.main()
