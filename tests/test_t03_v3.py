"""T03 V3 boundaries: preserve rules and keep target math independent of recipes."""
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.services import household, meal_request
from nutrition_planning.targets import targets_from_t03
from nutrition_target import TargetInputError
from scripts.verify_t03_v3 import require_v3, synthetic_members


@pytest.mark.parametrize('database,host,port', [
    ('meal_planner_1102_v1', '127.0.0.1', 55434),
    ('meal_planner_1102_v1', '127.0.0.1', 55435),
    ('meal_planner_1102_v3_t02_test', '127.0.0.1', 55435),
    ('meal_planner_1102_v3', '127.0.0.2', 55435),
    ('meal_planner_1102_v3', '127.0.0.1', 55434),
])
def test_verification_rejects_other_database_targets(database, host, port):
    with pytest.raises(ValueError, match='dedicated local V3'):
        require_v3(make_url(f'postgresql+psycopg://synthetic:unused@{host}:{port}/{database}'))


def test_member_daily_target_is_not_scaled_by_household_portion():
    on, members = synthetic_members()
    adult = next(iter(members.values()))
    daily = household.daily_target(adult, on=on)
    smaller = SimpleNamespace(**{**vars(adult), 'portion': Decimal('0.7')})
    assert household.daily_target(smaller, on=on) == daily
    assert daily['rule_version'] == 'hpa-dri8-mvp-0.3.0'
    assert daily['rule_config_sha256'] == 'fe74148eb24fd6dd036fd9ef5c64745a5413ee4c803470bc36e96b2c3f9a0aba'
    assert {k: Decimal(v) for k, v in daily['daily_reference'].items()} == {
        'energy_kcal': Decimal('1942.8591'), 'protein_g': Decimal('77')}
    assert not daily['approved_for_adoption']


def test_questionnaire_and_t12_bridge_keep_exact_bento_targets_without_recipe_input():
    on, members = synthetic_members()
    adult, child = members.values()
    draft = meal_request.MealRequestDraft(meal_date=on, meal_type='dinner', participants=[
        {'member_id': adult.id, 'bento': True}, {'member_id': child.id}])
    preview = meal_request.target_preview(draft, members)
    participations = meal_request.target_participations(draft, members)
    aggregate = household.target_service().aggregate_meal_targets(participations)
    assert len(participations) == 3
    assert [(p['kind'], p['meal']) for p in participations] == [
        ('dining', 'dinner'), ('bento', 'lunch'), ('dining', 'dinner')]
    assert Decimal(preview['total_n']) == Decimal('3.3')
    assert preview['target'] == aggregate['target']
    assert {k: Decimal(v) for k, v in preview['target'].items()} == {
        'energy_kcal': Decimal('2043.53067828'), 'protein_g': Decimal('78.694')}
    t12 = targets_from_t03(aggregate)
    assert t12.reference == {'energy_kcal': Fraction(17029422319, 27500000),
                             'protein_g': Fraction(3577, 150)}
    assert not preview['approved_for_adoption']


def test_recipe_fields_are_not_accepted_as_t03_member_inputs():
    on, members = synthetic_members()
    adult = next(iter(members.values()))
    fields = {k: getattr(adult, k) for k in (
        'birth_date', 'equation_sex', 'weight_kg', 'activity_category', 'goal')}
    member = household.target_input(fields, member_id=str(adult.id), on=on)
    with pytest.raises(TargetInputError) as error:
        household.target_service().daily_target({**member, 'recipe_id': 'R0254'})
    assert error.value.reason == 'UNKNOWN_FIELD'
