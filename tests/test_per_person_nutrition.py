"""2026-11-03：每人營養照 t11-t20-demo 呈現——用餐與便當分開，對照每日建議攝取量。"""
from decimal import Decimal

from app.services.per_person_nutrition import daily_reference, people


def _part(kind, share, energy='500'):
    return {'participation_id': f'm1:{kind}', 'member_id': 'm1', 'kind': kind, 'meal': 'lunch', 'n': '1',
            'share': share, 'meal_ratio': '3/10', 'dish_share': share, 'dish_ids': ['R1'], 'staple_g': '160',
            'daily': {'energy_kcal': '1684', 'protein_g': '121/2'},
            'nutrition': {'energy_kcal': energy, 'protein_g': '17'},
            'nutrition_all': {'energy_kcal': energy, 'protein_g': '17', 'fat_g': None, 'sodium_mg': '600'},
            'nutrition_known_partial': {'fat_g': '9'}}


def test_dining_and_bento_are_one_person_with_bento_listed_apart():
    [person] = people([_part('dining', '0.5'), _part('bento', '0.5')], {'m1': '媽媽'})
    assert person['name'] == '媽媽' and person['kinds'] == ['dining', 'bento']
    assert person['dining']['intake']['energy_kcal'] == '500' and person['bento'] is not None
    assert person['dining']['known_partial'] == {'fat_g': '9'}
    assert person['daily']['energy_kcal']['value'] == '1684'


def test_daily_reference_follows_the_health_promotion_rules():
    ref = daily_reference(Decimal('2000'), Decimal('60'))
    assert ref['carbohydrate_g']['kind'] == 'range'
    assert Decimal(ref['carbohydrate_g']['min']) == 250 and Decimal(ref['carbohydrate_g']['max']) == 300
    assert ref['sodium_mg'] == {'kind': 'max', 'max': '2400', 'label': '每日不超過 2400 mg'}
    assert Decimal(ref['dietary_fiber_g']['value']) == 28
