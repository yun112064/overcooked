from dataclasses import replace
from fractions import Fraction as F
import importlib
import json
from pathlib import Path

import pytest

from nutrition_planning import NutritionPolicy, calculate_nutrition
from nutrition_planning.integration import (RecipeLineProfile, RecipeRows,
    prepare_database_recipe, recipe_line_digest)
from nutrition_planning.t04_bridge import T04CookingResult, apply_t04_results


@pytest.fixture
def cooked(tmp_path, monkeypatch):
    # Execute the real T04 calculator/parameter validator with an injected
    # estimator; no API calls, keys, or invented model access.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'nutrition_model'))
    t04 = importlib.import_module('cooked_nutrition_service')
    raw: dict = dict(ingredient_id='raw-sample', standard_name='測試食材', state='raw',
        nutrition_per_100g={k: v for k, v in zip(t04.NUTRIENTS, [200, 10, 20, 2, 1, 5, 1])},
        source=dict(provider='TFDA', source_id='raw-sample', source_version='synthetic-v1'))
    # Order the required fields explicitly, independent from T04 tuple ordering.
    raw['nutrition_per_100g'].update(energy_kcal=200, protein_g=20)
    monkeypatch.setattr(t04.yields, 'get_yield_references', lambda *a, **k: [
        dict(reference_id='test-reference', match_type='analogy', cooking_yield_pct=75)])
    def estimator(payload):
        return dict(yield_factor=0.75, reference_ids=['test-reference'],
            nutrient_retention_factors={k: 0.8 for k in t04.NUTRIENTS},
            assumptions=['Synthetic test estimate, not an official USDA retention factor.'], missing_information=[])
    context = {'juices_consumed': False, 'added_oil_g': 0}
    result = t04.calculate_cooking('測試食材', 100, '烤', [raw], lambda name: raw,
                                  cooking_context=context, estimator=estimator)
    assert result['cooked_weight_g'] == 75 and result['is_estimated']
    recipe = dict(recipe_id='R', original_servings=1, review_status='approved', is_eligible=True)
    row = dict(recipe_ingredient_id='line', recipe_id='R', sequence=1, food_id='FOOD000001',
               weight_g=100, weight_status='source_explicit', unit='g', is_required=True,
               adjustment_status='not_reviewed')
    digest = recipe_line_digest(recipe, row)
    profile = RecipeLineProfile('FOOD000001', digest, 'raw', False, 1, 'unit-review', 'v1',
                               review_status='approved', cooking_method='烤', cooking_context=context)
    receipt = T04CookingResult(digest, 'FOOD000001', 'raw', '100', '烤', context,
                              'T04-synthetic-code+estimator-v1', result)
    catalog = tmp_path / 'tfda.json'
    catalog.write_text(json.dumps([raw]), encoding='utf-8')
    return RecipeRows(('R',), (recipe,), (row,)), {'line': profile}, NutritionPolicy(
        catalog, {'FOOD000001': 'raw-sample'}, ('energy_kcal', 'protein_g')), receipt


def test_real_t04_llm_parameters_feed_exact_t12_nutrition(cooked):
    rows, profiles, policy, receipt = cooked
    resolved, profiles, issues = apply_t04_results(rows, policy=policy, profiles=profiles, results={'line': receipt})
    assert not issues
    prepared = prepare_database_recipe(rows, policy=resolved, profiles=profiles)
    assert not prepared.issues
    assert prepared.recipe is not None
    result = calculate_nutrition(prepared.recipe, policy=resolved)
    assert result.totals == {'energy_kcal': F(160), 'protein_g': F(16)}
    assert result.sources[0]['raw_edible_weight_g'] == 100
    evidence = result.sources[0]['cooking_evidence']
    assert isinstance(evidence, dict)
    assert evidence['source'] == 'T04_LLM' and evidence['is_estimated'] is True
    assert evidence['yield_factor'] == F(3, 4)  # Kept separate from nutrient retention.
    assert evidence['retention']['energy_kcal'] == F(4, 5)
    assert evidence['reference_ids'] == ('test-reference',)
    assert evidence['source_version'] == receipt.source_version
    assert resolved.approved_for_adoption is False  # Coefficient policy != whole-system approval.


@pytest.mark.parametrize('mutation', ['digest', 'weight', 'method', 'context', 'bad_retention',
                                     'fake_reference', 'missing_retention', 'sample', 'nan',
                                     'reported_weight', 'hidden_estimate'])
def test_wrong_request_or_invalid_t04_output_is_rejected(cooked, mutation):
    rows, profiles, policy, receipt = cooked
    if mutation == 'digest': receipt = replace(receipt, input_digest='stale')
    elif mutation == 'weight': receipt = replace(receipt, input_weight_g='200')
    elif mutation == 'method': receipt = replace(receipt, cooking_method='煎')
    elif mutation == 'context': receipt = replace(receipt, cooking_context={'juices_consumed': True})
    elif mutation == 'bad_retention': receipt.result['nutrient_sources']['protein_g']['retention_factor'] = 1.2
    elif mutation == 'missing_retention': receipt.result['nutrient_sources']['protein_g']['retention_factor'] = None
    elif mutation == 'fake_reference': receipt.result['reference_ids'] = ['not-in-T04-references']
    elif mutation == 'sample': receipt.result['nutrient_sources']['protein_g']['source']['source_id'] = 'wrong'
    elif mutation == 'nan': receipt.result['llm_estimate']['yield_factor'] = float('nan')
    elif mutation == 'reported_weight': receipt.result['raw_weight_g'] = 200
    elif mutation == 'hidden_estimate': receipt.result['is_estimated'] = False
    resolved, _, issues = apply_t04_results(rows, policy=policy, profiles=profiles, results={'line': receipt})
    assert issues and not resolved.approved_evidence


def test_existing_cooked_tfda_nutrition_does_not_receive_llm_retention_again(cooked):
    rows, profiles, policy, receipt = cooked
    data = json.loads(policy.catalog_path.read_text(encoding='utf-8'))
    data[0]['state'] = 'cooked'
    policy.catalog_path.write_text(json.dumps(data), encoding='utf-8')
    for item in receipt.result['nutrient_sources'].values():
        item['basis'] = 'cooked_tfda'
    resolved, profiles, issues = apply_t04_results(rows, policy=policy, profiles=profiles, results={'line': receipt})
    assert not issues
    prepared = prepare_database_recipe(rows, policy=resolved, profiles=profiles)
    assert prepared.recipe is not None
    result = calculate_nutrition(prepared.recipe, policy=resolved)
    assert result.sources[0]['cooked_edible_weight_g'] == 75
    assert result.totals == {'energy_kcal': F(150), 'protein_g': F(15)}
