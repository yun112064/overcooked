"""Synthetic source records test AI lookup; no LLM or real database is used."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import reconciled_policy as policy
from app.services.meal_calculation import IngredientLine
from nutrition_planning.integration import RecipeRows, ALL_NUTRIENTS


@pytest.fixture
def calculator(monkeypatch):
    source = {'provider': 'TFDA', 'nutrition_id': 'n1', 'nutrition_source_id': 's1',
              'nutrition_policy_manifest_sha256': 'testhash'}
    record = {'state': 'raw', 'source': source,
              'nutrition_per_100g': {key: None for key in ALL_NUTRIENTS}}
    record['nutrition_per_100g']['energy_kcal'] = 100
    result = SimpleNamespace(line_records={'RI1': record, 'RI2': deepcopy(record)}, issues=[])
    rows = SimpleNamespace(recipes=(), ingredients=())
    # make_calculator uses dataclasses.replace; supply the real loaded-row shape.
    import inspect
    fields = inspect.signature(RecipeRows).parameters
    rows = RecipeRows(**{name: () for name in fields})
    rows = replace(rows, recipes=({'recipe_id': 'R1'},), ingredients=tuple(
        {'recipe_ingredient_id': lid, 'recipe_id': 'R1', 'food_id': 'FOOD1'} for lid in ('RI1', 'RI2')))
    monkeypatch.setattr(policy, 'load_policy', lambda: {'recipe_release_id': 'release1'})
    monkeypatch.setattr(policy, 'validate_database', lambda *args: None)
    monkeypatch.setattr(policy, 'load_database_recipe_rows', lambda *args: rows)
    monkeypatch.setattr(policy, 'resolve_rows', lambda *args: result)
    db = SimpleNamespace(scalars=lambda *args: ['R1'], connection=lambda: None)
    @contextmanager
    def factory():
        yield db
    return lambda: policy.make_calculator(factory), result


def test_ai_uses_only_unique_verified_release_food_values(calculator):
    build, _ = calculator
    line = IngredientLine('FOOD1', 'AI name is not a source', Decimal(200))
    result = build()([(line, Decimal(200))])[0]
    assert not result.error
    assert result.nutrients['energy_kcal'] == 200
    assert result.nutrients['protein_g'] is None
    assert result.trace['nutrition_source_id'] == 's1'
    assert result.trace['nutrition_policy_resolution'] == 'food_id_source'


@pytest.mark.parametrize('change', ['source', 'value', 'blocked'])
def test_conflicting_or_unapproved_foods_are_never_guessed(calculator, change):
    build, sources = calculator
    if change == 'source':
        sources.line_records['RI2']['source']['nutrition_source_id'] = 'different'
    elif change == 'value':
        sources.line_records['RI2']['nutrition_per_100g']['energy_kcal'] = 101
    else:
        sources.line_records.pop('RI2')
        sources.issues.append(SimpleNamespace(line_id='RI2', code='nutrition_recipe_derived_only'))
    result = build()([(IngredientLine('FOOD1', 'food', Decimal(100)), Decimal(100))])[0]
    assert result.error
    assert all(v is None for v in result.nutrients.values())


@pytest.mark.parametrize('fields', [
    {'food_id': 'unknown'}, {'recipe_ingredient_id': 'fake'},
    {'recipe_release_id': 'other'}, {'recipe_publication_id': 'unbound'}, {'weight_basis': 'cooked'},
])
def test_ai_lookup_cannot_bypass_line_version_or_state_gates(calculator, fields):
    build, _ = calculator
    line = replace(IngredientLine('FOOD1', 'food', Decimal(100)), **fields)
    assert build()([(line, Decimal(100))])[0].error


def test_candidate_filter_uses_policy_result_and_preserves_unknown_optional_values():
    from app.services.generation import FoodDictionary, uncomputable_policy_food_ids
    foods = FoodDictionary([(fid, fid, [], []) for fid in ('approved', 'derived', 'cooked', 'missing')],
                           {fid: 1 for fid in ('approved', 'derived', 'cooked', 'missing')})
    def calculator(pairs):
        return [SimpleNamespace(error={'derived': 'nutrition_recipe_derived_only',
                    'cooked': 'nutrition_policy_food_state_mismatch'}.get(line.food_id),
                nutrients={'energy_kcal': 100, 'protein_g': None if line.food_id == 'missing' else 10,
                           'dietary_fiber_g': None}) for line, _ in pairs]
    assert uncomputable_policy_food_ids(foods, calculator) == {'derived', 'cooked', 'missing'}
