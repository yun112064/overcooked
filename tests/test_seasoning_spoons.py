"""2026-11-03：畫面上調味料用小匙／大匙顯示（沿用 t11-t20-demo 的 config/seasoning_ingredients.json）。"""
from decimal import Decimal

from app.services.seasonings import spoon_display
from app.services.t12_planning import _with_spoons

SALT = 'FOOD000691'  # 食鹽（鹽，每小匙 5 克）


def test_salt_grams_become_spoons_and_tiny_amounts_say_a_little():
    assert spoon_display(SALT, Decimal('2.5'), 'g') == {'display_amount': Decimal('0.50'), 'display_unit': '小匙',
                                                       'display_text': None}
    assert spoon_display(SALT, Decimal('15'), 'g')['display_unit'] == '大匙'
    assert spoon_display(SALT, Decimal('0.3'), 'g')['display_text'] == '少許'


def test_exact_fraction_grams_and_non_seasonings_keep_grams():
    shown = _with_spoons({'name': '食鹽', 'food_id': SALT, 'unit': 'g', 'grams': '5/2'})
    assert shown['display_unit'] == '小匙'
    plain = _with_spoons({'name': '雞胸肉', 'food_id': 'FOOD000228', 'unit': 'g', 'grams': '84'})
    assert plain['display_amount'] is None and plain['display_text'] is None
