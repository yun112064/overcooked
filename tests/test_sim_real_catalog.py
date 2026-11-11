from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.services import sim_checkout
from app.source_adapters.price_models import PriceQuote, ProductCandidate, MatchDecision


def quote(**overrides):
    values = dict(canonical_name='板豆腐', food_ids=['F1'], source_url='https://shop.example/product/1',
        queried_at=datetime.now(timezone.utc), selection_tier='auto_adopted', confidence='high', availability='in_stock', status='success', price=Decimal('20'), currency='TWD',
        package_quantity=Decimal('300'), package_unit='g', product_id='P1', product_name='板豆腐300g',
        recipe_release_id='release-test', snapshot_id=1,
        candidates=[ProductCandidate(product_id='P1', product_name='板豆腐300g', match_decision=MatchDecision.MATCH,
            raw={'image':'https://images.example/tofu.jpg'})])
    values.update(overrides)
    return PriceQuote(**values)


def test_catalog_keeps_missing_and_expired_ingredients_out_of_purchasable_products(monkeypatch):
    monkeypatch.setattr(sim_checkout, 'latest_quotes', lambda *a, **kw: {
        'F1': quote(), 'F3': quote(food_ids=['F3'], valid_until=datetime.now(timezone.utc)-timedelta(days=1))})
    result=sim_checkout.real_catalog(None,'release-test',{'F1':'板豆腐','F2':'洋蔥','F3':'雞蛋'})
    assert result['ingredient_count']==3
    assert [p['food_id'] for p in result['products']]==['F1']
    assert result['products'][0]['price_cents']==2000
    assert result['products'][0]['image_url']=='https://images.example/tofu.jpg'
    assert {p['food_id']:p['reason'] for p in result['unavailable_ingredients']}=={'F2':'NO_PRICE_DATA','F3':'PRICE_EXPIRED'}
    assert all('price_cents' not in p for p in result['unavailable_ingredients'])


def test_source_image_never_borrows_another_products_image():
    assert sim_checkout.source_image(quote(product_id='OTHER')) is None


def test_existing_garlic_shoot_quote_is_not_a_garlic_bulb_price(monkeypatch):
    from app.source_adapters.product_matching import evaluate_match, parse_package_details
    from app.source_adapters.price_models import IngredientQuery
    product = '大蒜(蒜苗)180g'
    for name in ('大蒜', '蒜頭'):
        decision = evaluate_match(IngredientQuery(canonical_name=name, food_ids=['F1']), product,
                                  parse_package_details(product))
        assert decision[0] is MatchDecision.REJECT
        monkeypatch.setattr(sim_checkout, 'latest_quotes', lambda *a, **kw: {
            'F1': quote(canonical_name=name, product_name=product)})
        catalog = sim_checkout.real_catalog(None, 'release-test', {'F1':name})
        assert catalog['products'] == []
        assert catalog['unavailable_ingredients'][0]['reason'] == 'PRODUCT_IDENTITY_CONFLICT'


def test_source_image_rejects_active_content_and_credentials():
    for image in ['javascript:alert(1)', 'data:image/png;base64,eA==','https://user:secret@images.example/a.jpg']:
        q=quote()
        q.candidates[0].raw['image']=image
        assert sim_checkout.source_image(q) is None


def test_real_catalog_does_not_sell_other_release_or_fractional_pack(monkeypatch):
    monkeypatch.setattr(sim_checkout,'latest_quotes',lambda *a,**kw:{'F1':quote(recipe_release_id='old'),
        'F2':quote(package_quantity=Decimal('0.5'))})
    result=sim_checkout.real_catalog(None,'release-test',{'F1':'板豆腐','F2':'洋蔥'})
    assert result['products']==[]
    assert len(result['unavailable_ingredients'])==2

def test_reopening_receipt_preserves_full_catalog_and_frozen_order(monkeypatch):
    from fastapi import Request, Response
    from app.api import sim_market
    receipt={'recipe_release_id':'old-release','lines':[{'food_id':'F1','price_cents':2000}]}
    full={'recipe_release_id':'release-test','products':[{'food_id':'F1','price_cents':2500},{'food_id':'F2','price_cents':3900}]}
    monkeypatch.setattr(sim_market.settings,'sim_market_real_checkout_enabled',True)
    monkeypatch.setattr(sim_market.market_handoff,'market_origin',lambda:'http://localhost:8241')
    monkeypatch.setattr(sim_market.market_handoff,'redeem',lambda *a:{'handoff_id':'test'})
    monkeypatch.setattr(sim_market,'get_catalog',lambda db:full)
    request=Request({'type':'http','headers':[(b'origin',b'http://localhost:8241')]})
    result=sim_market.checkout(sim_market.CheckoutBody(token='test'),request,Response(),
        SimpleNamespace(scalar=lambda _:SimpleNamespace(receipt=receipt)))
    assert result['catalog']==full
    assert result['order']==receipt
    assert result['order']['lines'][0]['price_cents']==2000
