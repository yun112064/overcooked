"""Deterministic ingredient identity checks: no database or paid model calls."""
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from app.services import generation as g
from app.services.llm_client import StructuredResponse

BREAST, LEG = 'FOOD000101', 'FOOD000102'


def foods():
    return g.FoodDictionary([
        (BREAST, '雞胸肉', ['雞胸'], ['雞肉']),
        (LEG, '雞腿肉', ['雞腿'], ['雞肉']),
    ], {BREAST: 1, LEG: 99})


def payload(name='雞胸肉', **extra):
    return {'dishes': [{'dish_name': '蒸雞胸', 'aliases': [], 'cooking_methods': ['蒸'],
                       'steps': ['蒸至熟'], 'ingredients': [{'name': name, 'grams_per_n': 80, **extra}]}]}


def convert(name='雞胸肉', **extra):
    return g._ai_dishes(g.MenuCandidate.model_validate(payload(name, **extra)), foods(), [])


def test_external_schema_never_asks_model_for_food_id():
    schema = g.strict_schema(g.NamedMenuCandidate)
    assert 'food_id' not in json.dumps(schema)
    ingredient = schema['$defs']['NamedAiIngredient']
    assert set(ingredient['required']) == {'name', 'grams_per_n'}
    assert ingredient['additionalProperties'] is False
    with pytest.raises(ValidationError):
        g.NamedMenuCandidate.model_validate(payload(food_id=LEG))
    assert '請填其 food_id' not in g.MENU_INSTRUCTIONS


def test_names_only_response_resolves_breast_not_more_frequent_leg():
    response = StructuredResponse('completed', output_text=json.dumps(payload(), ensure_ascii=False))
    candidate, failure = g._parse(response, g.MenuCandidate)
    assert not failure and candidate.dishes[0].ingredients[0].food_id is None
    dishes, lines, problems, resolutions = g._ai_dishes(candidate, foods(), [])
    assert not problems and not resolutions
    assert dishes[0]['food_ids'] == [BREAST]
    assert lines[0].food_id == BREAST and lines[0].grams_per_n == Decimal(80)


def test_legacy_breast_name_and_leg_id_is_still_rejected():
    _, lines, problems, _ = convert(food_id=LEG)
    assert not lines
    assert problems[0]['code'] == 'INGREDIENT_ID_MISMATCH'
    assert problems[0]['submitted_food_id'] == LEG
    assert problems[0]['candidate_food_ids'] == [BREAST]


def test_consistent_legacy_id_remains_compatible():
    _, lines, problems, _ = convert(food_id=BREAST)
    assert not problems and lines[0].food_id == BREAST


@pytest.mark.parametrize('legacy_id', [None, BREAST, LEG])
def test_ambiguous_name_is_never_chosen_by_frequency_or_model_id(legacy_id):
    _, lines, problems, resolutions = convert('雞肉', food_id=legacy_id)
    assert not lines and not resolutions
    assert problems[0]['code'] == 'INGREDIENT_AMBIGUOUS'
    assert problems[0]['candidate_food_ids'] == [BREAST, LEG]


def test_unique_alias_still_must_match_allowed_canonical_name():
    _, lines, problems, _ = convert('雞胸')
    assert not lines and problems[0]['code'] == 'INGREDIENT_NOT_ALLOWED'


def test_unknown_name_cannot_hide_behind_a_known_legacy_id():
    _, lines, problems, _ = convert('不存在的食材', food_id=LEG)
    assert not lines and problems[0]['code'] == 'INGREDIENT_UNRESOLVED'


def test_conflicting_canonical_names_are_removed_from_allowed_list():
    dictionary = g.FoodDictionary([(BREAST, '雞肉', [], []), (LEG, '雞肉', [], [])], {BREAST: 1, LEG: 100})
    assert dictionary.allowed_names() == []
    assert dictionary.resolve('雞肉', None) == (None, 'INGREDIENT_AMBIGUOUS')


def test_allowlist_excludes_forbidden_or_uncomputable_identity():
    candidate = g.MenuCandidate.model_validate(payload())
    _, lines, problems, _ = g._ai_dishes(candidate, foods(), [], allowed_names=['雞腿肉'])
    assert not lines and problems[0]['code'] == 'INGREDIENT_NOT_ALLOWED'


def test_available_leg_stock_does_not_assign_leg_id_to_breast():
    draft = SimpleNamespace(available_ingredient_ids=[LEG], meal_type='dinner', staple=None,
                            dish_count=1, specified_dishes=['蒸雞胸'], taste_preferences=[], budget=None)
    data = json.loads(g._menu_input(draft, {'excluded_food_ids': []}, foods(), {'excluded_dishes': []},
                                   {'total_n': '1', 'target': {'energy_kcal': '500', 'protein_g': '30'}}))
    assert data['known_ingredients'] == ['雞腿肉']
    assert data['allowed_ingredients'] == ['雞胸肉', '雞腿肉']
    assert BREAST not in json.dumps(data) and LEG not in json.dumps(data)
    dishes, _, problems, _ = g._ai_dishes(g.MenuCandidate.model_validate(payload()), foods(), [],
                                         allowed_names=data['allowed_ingredients'])
    assert not problems and dishes[0]['food_ids'] == [BREAST]


def test_external_enum_excludes_ambiguous_and_request_forbidden_names():
    dictionary = g.FoodDictionary([
        (BREAST, '雞胸肉', [], []), (LEG, '雞腿肉', [], []),
        ('FOOD000103', '鹽', [], []), ('FOOD000104', '鹽', [], []),
    ], {BREAST: 1, LEG: 1, 'FOOD000103': 1, 'FOOD000104': 1})
    allowed = dictionary.allowed_names(exclude=[LEG])
    assert allowed == ['雞胸肉']
    schema = g.menu_candidate_schema(allowed)
    validator = Draft202012Validator(schema)
    validator.validate(payload())
    for forbidden in ['雞腿肉', '鹽', '雞胸', '不存在的食材']:
        assert not validator.is_valid(payload(forbidden))
        _, lines, problems, _ = g._ai_dishes(g.MenuCandidate.model_validate(payload(forbidden)),
                                            dictionary, [], allowed_names=allowed)
        assert not lines and problems
    assert not validator.is_valid(payload(food_id=BREAST))


def test_request_enums_are_independent_and_keep_exact_names():
    first = g.menu_candidate_schema(['雞胸肉', '雞胸肉'])
    second = g.menu_candidate_schema(['雞腿肉'])
    assert first['$defs']['NamedAiIngredient']['properties']['name']['enum'] == ['雞胸肉']
    assert second['$defs']['NamedAiIngredient']['properties']['name']['enum'] == ['雞腿肉']
    assert Draft202012Validator(second).is_valid(payload('雞腿肉'))
    assert not Draft202012Validator(second).is_valid(payload('雞胸肉'))


@pytest.mark.parametrize('allowed', [[], [''], ['  '], [None], ['雞胸肉', '']])
def test_empty_or_invalid_allowlist_fails_before_external_generation(allowed):
    with pytest.raises(g.jobs.JobFailure) as failure:
        g.menu_candidate_schema(allowed)
    assert failure.value.code == 'NO_ALLOWED_INGREDIENTS'


def test_retry_feedback_never_suggests_request_excluded_candidates():
    original = [{'code': 'INGREDIENT_AMBIGUOUS', 'ingredient': '鹽', 'candidate_names': ['鹽']},
                {'code': 'INGREDIENT_NOT_ALLOWED', 'ingredient': '雞肉',
                 'candidate_names': ['雞胸肉', '雞腿肉']}]
    result = g.menu_retry_feedback(original, ['雞胸肉'])
    assert result[0]['candidate_names'] == []
    assert result[1]['candidate_names'] == ['雞胸肉']
    assert original[1]['candidate_names'] == ['雞胸肉', '雞腿肉']
    assert 'candidate_names 為空時' in g.MENU_INSTRUCTIONS


@pytest.mark.parametrize('grams', [0, -1, 2000.01, float('inf'), float('-inf'), float('nan')])
def test_external_quantity_rejects_invalid_values_but_internal_reports_them(grams):
    data = payload(grams_per_n=grams)
    with pytest.raises(ValidationError):
        g.NamedMenuCandidate.model_validate(data)
    candidate = g.MenuCandidate.model_validate(data)
    _, lines, problems, _ = g._ai_dishes(candidate, foods(), [])
    assert not lines and problems[0]['code'] == 'INVALID_QUANTITY'
    assert 'grams_per_n' in problems[0]
    if grams == grams:
        assert problems[0]['grams_per_n'] == grams


def test_external_quantity_schema_bounds_and_valid_endpoints():
    schema = g.menu_candidate_schema(['雞胸肉'])
    quantity = schema['$defs']['NamedAiIngredient']['properties']['grams_per_n']
    assert quantity['exclusiveMinimum'] == 0
    assert quantity['maximum'] == 2000
    validator = Draft202012Validator(schema)
    for grams in [0.01, 2000]:
        validator.validate(payload(grams_per_n=grams))
        g.NamedMenuCandidate.model_validate(payload(grams_per_n=grams))
    for grams in [0, -0.01, 2000.01]:
        assert not validator.is_valid(payload(grams_per_n=grams))
    assert '不用的食材直接省略，不得輸出零克' in g.MENU_INSTRUCTIONS
