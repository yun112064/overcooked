"""Capacity source evidence and backward-compatible management contracts."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.errors import ApiError
from app.management.contracts import validate
from app.services.recipe_volume import attach_preview_volumes, source_volume_ml, volume_in_ml
from app.services.meal_calculation import IngredientLine, _row, pricing_requirements
from recipe_workbook import build, ingredient, recipe
from app.services.recipe_import import parse_recipe_workbook
from test_management_price import correction
from test_management_recipe_candidates import candidate, request, intake, recipe_release
from test_management_foundation import ACTORS, env


@pytest.mark.parametrize(('amount', 'unit', 'expected'), [
    ('0.5', 'L', '500'), ('100', 'mL', '100'), ('2', '公升', '2000'),
    ('0.25', '升', '250'), ('100', 'g', None), ('NaN', 'mL', None), ('0', 'L', None),
])
def test_normalize_only_explicit_capacity(amount, unit, expected):
    assert volume_in_ml(amount, unit) == (Decimal(expected) if expected else None)


def test_source_capacity_requires_matching_quantity_and_evidence():
    source = SimpleNamespace(quantity_status='source_exact', volume_evidence='workbook_volume',
                             volume_ml=Decimal(500), amount=Decimal('0.5'), unit='L')
    assert source_volume_ml(source) == 500
    for key, value in [('quantity_status', 'reference_amount'), ('volume_ml', Decimal(600)),
                       ('volume_evidence', 'estimate:tablespoon'), ('amount', None)]:
        altered = SimpleNamespace(**{**vars(source), key: value})
        assert source_volume_ml(altered) is None


@pytest.mark.parametrize('unit', ['mL', 'ML', '毫升', 'L', '升'])
def test_import_preserves_original_capacity_and_missing_mass(tmp_path, unit):
    item = ingredient('F_VOLUME', '合成液體', None, raw_text=f'合成液體 0.5 {unit}',
                      amount=Decimal('0.5'), unit=unit)
    book = build(tmp_path / 'volume.xlsx', [recipe('R001', '合成菜色', ingredients=[item])])
    row = parse_recipe_workbook(book).recipe_ingredients[0]
    assert row['source_unit'] == unit
    assert row['weight_g'] is None
    assert source_volume_ml(SimpleNamespace(**row)) == volume_in_ml('0.5', unit)


def test_volume_contract_accepts_old_mass_and_new_volume_without_rewriting():
    original = correction(sources=[dict(artifact_id='fixture', artifact_sha256='a' * 64,
                                        origin_kind='manual_entry', source_policy_version_id=None)])
    before = deepcopy(original)
    assert validate('PriceContent', original) == before
    capacity = {**original, 'package_mass_g': None, 'package_volume_ml': '500'}
    assert validate('PriceContent', capacity) == capacity
    for invalid in [
        {**original, 'package_volume_ml': '500'},
        {**capacity, 'package_volume_ml': None},
        {**capacity, 'package_volume_ml': '0'},
        {**capacity, 'package_volume_ml': '-1'},
        {**capacity, 'package_volume_ml': 500},
    ]:
        with pytest.raises(ApiError):
            validate('PriceContent', invalid)


def test_partial_unknown_demand_cannot_disappear_behind_known_food():
    known = IngredientLine('FOOD', '合成液體', None, pricing_volume_ml_per_n=Fraction(100, 3))
    unknown = IngredientLine('FOOD', '合成液體', None)
    rows = pricing_requirements([known, unknown], [None, None], Decimal(1))
    assert len(rows) == 2
    assert rows[0].volume_ml == Fraction(100, 3)
    assert rows[1].grams is None and rows[1].volume_ml is None
    displayed = _row(known, None, None, None, Decimal(1))
    assert displayed['volume_ml'] == '100/3'
    assert displayed['whole_meal_grams'] is None and displayed['nutrients'] is None


def test_picker_capacity_uses_exact_line_identity_and_actual_scaled_mass():
    source = SimpleNamespace(recipe_ingredient_id='line', recipe_id='recipe', quantity_status='source_exact',
                             volume_evidence='workbook_volume', volume_ml=Decimal(500),
                             amount=Decimal('0.5'), unit='L')
    db = Mock()
    db.scalars.return_value = [source]
    result = SimpleNamespace(line_labels={'line': {'grams': '600'}})
    payload = {'dishes': [{'recipe_id': 'recipe', 'adjusted': True,
                          'ingredients': [{'line_id': 'line', 'grams': '200', 'food_id': 'FOOD'}]}]}
    attach_preview_volumes(db, result, payload)
    line = payload['dishes'][0]['ingredients'][0]
    assert line['volume_ml'] == '500/3' and line['grams'] == '200'
    payload['dishes'][0]['recipe_id'] = 'other-recipe'
    attach_preview_volumes(db, result, payload)
    assert line['volume_ml'] is None


def test_capacity_candidate_keeps_missing_mass_and_requires_quantity_evidence(env, recipe_release):
    env.grants(ACTORS[1], ['recipe_editor'], ['recipe'])
    _, pins = recipe_release
    fixture = candidate()
    fixture['recipe']['ingredients'][0].update(amount='0.5', unit='L', weight_g=None,
                                              raw_text='魚肉 0.5 L')
    proposal = intake(env, 'capacity-candidate', request(fixture, pins))['proposal']
    line = proposal['content']['ingredients'][0]
    assert line['amount']['value'] == '0.5' and line['unit'] == 'L'
    assert line['weight_g']['value'] is None and proposal['status'] == 'draft'
    job = env.call(ACTORS[1], 'request_validation', env.key(), proposal['proposal_id'], env.expected(proposal))
    validation = env.call(ACTORS[1], 'run_validation', job['task_id'])
    codes = {issue['code'] for issue in validation['issues']}
    assert 'SOURCE_WEIGHT_REQUIRED' in codes
    assert 'SOURCE_VOLUME_REQUIRED' not in codes
    assert not validation['catalog_ready'] and not validation['formal_generation_ready']
    content = deepcopy(proposal['content'])
    content['ingredients'][0]['amount']['evidence_ids'] = []
    revised = env.call(ACTORS[1], 'revise', env.key(), proposal['proposal_id'], {
        **env.expected(proposal), 'domain': 'recipe', 'content': content, 'dependency_pins': pins,
    })
    job = env.call(ACTORS[1], 'request_validation', env.key(), proposal['proposal_id'], env.expected(revised))
    validation = env.call(ACTORS[1], 'run_validation', job['task_id'])
    assert 'SOURCE_VOLUME_EVIDENCE_REQUIRED' in {issue['code'] for issue in validation['issues']}
