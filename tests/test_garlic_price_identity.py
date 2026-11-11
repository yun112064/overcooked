"""Synthetic retail fixtures: raw peeled garlic is not sprout/supplement garlic."""
import pytest
from app.source_adapters.price_models import IngredientQuery
from app.source_adapters.pxmart import candidate_from_raw, build_price_quote_from_candidates


def quote(name):
    query = IngredientQuery(canonical_name='大蒜', food_ids=['FOOD000039'])
    candidate = candidate_from_raw(query, {'name': name, 'sku': 'fixture-garlic',
        'url': 'https://example.test/garlic', 'offers': {'price': '109',
        'priceCurrency': 'TWD', 'availability': 'https://schema.org/InStock'}})
    return build_price_quote_from_candidates(query, [candidate], source_url='https://example.test/garlic')


def test_raw_peeled_garlic_keeps_exact_package():
    result = quote('鮮藏蒜仁（產銷履歷） 120g')
    assert result.status.value == 'success'
    assert result.package_quantity == 120


def test_mirin_accepts_real_volume_package_without_gram_conversion():
    query = IngredientQuery(canonical_name='味醂', food_ids=['FOOD000445'])
    candidate = candidate_from_raw(query, {'name': '純釀味醂 500ml', 'sku': 'fixture-mirin',
        'offers': {'price': '100', 'priceCurrency': 'TWD',
                   'availability': 'https://schema.org/InStock'}})
    result = build_price_quote_from_candidates(query, [candidate], source_url='https://example.test/mirin')
    assert result.status.value == 'success'
    assert result.package_quantity == 500
    assert result.package_unit == 'ml'


@pytest.mark.parametrize('name', ['大蒜(蒜苗)180g', '大蒜精素膠囊 120g', '黑蒜仁 120g', '蒜仁酥 120g'])
def test_other_identity_is_not_a_purchasable_garlic(name):
    assert quote(name).status.value != 'success'


@pytest.mark.parametrize(('food', 'product'), [
    ('甘草', '甘草橄欖 140g'), ('百里香', '水晶液體皂2.2kg-百里香'),
    ('肉桂', '穀脆蘋果肉桂核桃 280g'), ('沙茶', '沙茶豆乾 270g'),
    ('味醂', '味醂漬嫩薑 380g'), ('清酒', '日式清酒壺 330ml'),
    ('生米', '喜生米漢堡 510g'), ('檸檬', 'LOTTE檸檬糖 17.5g'),
    ('鹹蛋黃', '鹹蛋黃蕎麥酥 210g'), ('蔭油', '甘醇蔭油膏 420g'),
    ('雞柳條', '香酥雞柳條 300g'), ('蠔油', '香菇素蠔油 4.4kg'),
    ('麵粉', '有機全麥麵粉 500g'),
    ('肉桂', '微米泡染髮凝膠 肉桂焦糖棕 195g'),
    ('沙茶', '蜜沙茶魚片105g'), ('清酒', '清酒杯90ml'),
    ('麵粉', '健康全粒麵粉 1000g'),
    ('沙茶', '爆汁方干-沙茶240g'), ('新鮮藍莓', '藍莓生貝果105g'),
    ('黃芥末醬', '蜂蜜黃芥末醬320g'), ('豆豉', '香菇豆豉400g'),
    ('豆腐乳', '梅子豆腐乳380g'),
    ('豆豉', '豆豉小魚240g'),
    ('豆豉', '有機鳳梨豆豉280g'),
])
def test_catalog_name_contains_ingredient_but_is_not_that_ingredient(food, product):
    from app.source_adapters.product_matching import identity_conflicts
    assert identity_conflicts(food, product)
