"""2026-11-03：顯示用營養缺值照 t11-t20-demo 補值——必然為 0 的類別規則優先，其餘用快取推估；TFDA 有值一律為準。"""
from fractions import Fraction as F

from nutrition_planning import nutrient_fill


def _record(sample_id, **values):
    base = {k: None for k in ('energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg',
                              'dietary_fiber_g')}
    return {'nutrition_per_100g': {**base, 'energy_kcal': F(100), 'protein_g': F(5), **values},
            'source': {'source_id': sample_id}}


def test_category_zero_fills_meat_fibre_and_is_traced():
    filled = nutrient_fill.fill_record(_record('I0404501', fat_g=F(3)))
    assert filled['nutrition_per_100g']['dietary_fiber_g'] == 0
    assert {'nutrient': 'dietary_fiber_g', 'basis': 'category_zero', 'rule': 'animal_flesh_no_fiber'} \
        in filled['source']['display_fills']


def test_cached_estimate_fills_and_tfda_value_always_wins():
    filled = nutrient_fill.fill_record(_record('A0320202'))  # 低筋麵粉: demo estimate for sugars
    assert filled['nutrition_per_100g']['sugars_g'] == F('0.3')
    assert any(f['basis'] == 'llm_estimated' and f['nutrient'] == 'sugars_g' for f in filled['source']['display_fills'])
    kept = nutrient_fill.fill_record(_record('A0320202', sugars_g=F(2)))
    assert kept['nutrition_per_100g']['sugars_g'] == 2


def test_validated_nutrients_are_never_filled():
    record = _record('I0404501')
    record['nutrition_per_100g']['protein_g'] = None
    assert nutrient_fill.fill_record(record)['nutrition_per_100g']['protein_g'] is None
