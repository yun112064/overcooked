"""Preview costs and adoption use synthetic prices; nutrition is independently stubbed."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.models import MealRequest
from app.models.recipe import Recipe, SourceArchive
from app.services import database_menu_plans, meal_cost
from db_support import requires_db
from test_meal_plans import CABBAGE, PASSED, _MealPlanApiBase
from test_t13_meal_cost import quote


def preview_with(*lines):
    preview = deepcopy(PASSED)
    preview['dishes'][0]['ingredients'] = [
        {'food_id': CABBAGE, 'name': '合成容量食材', 'grams': grams, 'volume_ml': volume}
        for grams, volume in lines
    ]
    return preview


def capacity_quote(price='150', qty='0.5', unit='L'):
    return quote(CABBAGE, price, qty, unit,
                 queried_at=datetime.now(timezone.utc) - timedelta(minutes=10))


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setattr(settings, 'management_price_policy_enabled', False)
    monkeypatch.setattr(meal_cost, 'cost_quote_candidates', lambda *_: {CABBAGE: [capacity_quote()]})


@pytest.mark.parametrize(('amounts', 'total', 'packs'), [
    (['100'], '30', 1),
    (['100/3', '200/3'], '30', 1),
    (['500.000001'], '150.0000003', 2),
])
def test_preview_capacity_cost_and_package_rounding(prices, amounts, total, packs):
    preview = preview_with(*[(None, amount) for amount in amounts])
    before = deepcopy(preview)
    result = database_menu_plans._cost(None, preview, SimpleNamespace(available_ingredient_ids=[], budget=Decimal(300)))
    assert Decimal(result['cost_total']) == Decimal(total)
    assert result['lines'][0]['packs'] == packs
    assert Decimal(result['purchase_total']) == 150 * packs
    assert result['lines'][0]['required_grams'] is None
    assert preview == before  # Pricing must not fill missing mass or alter nutrition.


def test_preview_unknown_component_is_not_hidden_by_known_capacity(prices):
    result = database_menu_plans._cost(None, preview_with((None, '100'), (None, None)),
                                      SimpleNamespace(available_ingredient_ids=[], budget=Decimal(300)))
    assert result['budget_status'] == 'PRICE_INCOMPLETE'
    assert result['unquantified_food_ids'] == [CABBAGE]
    assert Decimal(result['known_cost_total']) == 30
    assert result['cost_total'] is None
    assert any(line['price_status'] == 'unknown' for line in result['lines'])


def test_preview_preserves_independent_and_paired_dimensions(monkeypatch):
    monkeypatch.setattr(settings, 'management_price_policy_enabled', False)
    volume = capacity_quote().model_copy(update={'snapshot_id': 17})
    mass = volume.model_copy(update={'package_quantity': Decimal(600), 'package_unit': 'g',
                                     'weight_basis': 'manufacturer_net_mass'})
    monkeypatch.setattr(meal_cost, 'cost_quote_candidates', lambda *_: {
        CABBAGE: [volume, mass],
    })
    draft = SimpleNamespace(available_ingredient_ids=[], budget=Decimal(300))
    paired = database_menu_plans._cost(None, preview_with(('120', '100')), draft)
    assert Decimal(paired['cost_total']) == 30
    separate = database_menu_plans._cost(None, preview_with(('120', None), (None, '100')), draft)
    assert Decimal(separate['cost_total']) == 60
    assert separate['purchase_total'] is None  # Same SKU, unreconciled package dimensions.
    assert len(separate['lines']) == 2


def test_preview_manual_policy_receives_source_release(prices, monkeypatch):
    monkeypatch.setattr(settings, 'management_price_policy_enabled', True)
    with patch.object(meal_cost, 'active_overrides', return_value={}) as active:
        database_menu_plans._cost(None, preview_with((None, '100')),
            SimpleNamespace(available_ingredient_ids=[], budget=Decimal(300)), recipe_release_id='source-release')
    assert active.call_args.kwargs['recipe_release_id'] == 'source-release'
    assert active.call_args.kwargs['allow_volume'] is True


@requires_db
class PreviewCapacityAdoptionTests(_MealPlanApiBase):
    def setUp(self):
        super().setUp()
        from db_support import session_factory
        self.factory = session_factory()
        with self.factory() as db, db.begin():
            for recipe_id in ('R1', 'R2'):
                raw = 'capacity-' + recipe_id
                db.add(SourceArchive(raw_id=raw, recipe_release_id='legacy-1017', source_name='synthetic',
                    source_url='https://example.invalid/recipe', source_recipe_id=recipe_id,
                    recipe_name_raw=recipe_id, servings_raw='2', ingredients_raw='合成食材 0.5 L',
                    steps_raw='合成步驟', tags_raw='', content_hash='a' * 64, import_batch_id='capacity-fixture'))
                db.flush()  # Persist source evidence before inserting its FK-dependent recipe.
                db.add(Recipe(recipe_id=recipe_id, recipe_release_id='legacy-1017', raw_id=raw,
                    standard_dish_id='DISH-' + recipe_id, recipe_name=recipe_id, normalized_name=recipe_id,
                    servings_raw='2', servings_status='known', category='主菜', meal_type='晚餐',
                    cuisine='台式', source_name='synthetic', source_url='https://example.invalid/recipe'))
            row = db.get(MealRequest, self.request['id'])
            row.payload = {**row.payload, 'mode': 'picker', 'specified_dishes': ['蕃茄炒蛋', '炒青菜']}

    def priced_adopt(self, preview, price='150'):
        with patch.object(meal_cost, 'cost_quote_candidates', return_value={CABBAGE: [capacity_quote(price)]}):
            return self.adopt(['R1', 'R2'], preview=preview)

    def test_capacity_adoption_saves_cost_history_sources_and_cart_once(self):
        preview = preview_with((None, '100/3'))
        res = self.priced_adopt(preview)
        self.assertEqual(res.status_code, 201, res.text)
        plan = res.json()['meal_plan']
        path = f"/meal-plans/{plan['id']}"
        detail = self.client.get(path).json()
        snapshot = detail['snapshot']
        self.assertEqual(snapshot['schema_version'], 'database_menu_snapshot/v2')
        self.assertEqual(snapshot['versions']['recipe_release_id'], 'legacy-1017')
        self.assertEqual(snapshot['previous_day']['at_adoption']['previous_date'], '2026-08-19')
        self.assertEqual(set(snapshot['recipe_sources']), {'R1', 'R2'})
        self.assertEqual(Decimal(snapshot['cost']['cost_total']), 10)
        self.assertEqual(Decimal(snapshot['cost']['purchase_total']), 150)
        self.assertTrue(snapshot['eligibility']['adoptable'])
        self.assertEqual(snapshot['market_shortages']['items'], [
            {'food_id': CABBAGE, 'name': '合成容量食材', 'shortage_grams': None, 'shortage_ml': '100/3'}])
        self.assertIsNone(snapshot['dishes'][0]['ingredients'][0]['grams'])
        # Reopening and an idempotent retry do not consult newer prices or rewrite history.
        with patch.object(database_menu_plans, '_cost', side_effect=AssertionError('unexpected recost')):
            retry = self.priced_adopt(preview, price='9999')
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(self.client.get(path).json(), detail)

    def test_capacity_cost_over_budget_is_rejected_without_writes(self):
        res = self.priced_adopt(preview_with((None, '1000.0001')))
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()['error']['code'], 'BUDGET_EXCEEDED')
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [])
        self.assertNotEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()['status'], 'confirmed')

    def test_capacity_cost_still_rechecks_yesterday(self):
        # Explicit picker choices may repeat; exercise the non-exempt questionnaire path.
        with self.factory() as db, db.begin():
            row = db.get(MealRequest, self.request['id'])
            row.payload = {**row.payload, 'mode': 'questionnaire', 'specified_dishes': []}
        self.adopt_yesterday(['R1', 'R2'])
        res = self.priced_adopt(preview_with((None, '100')))
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()['error']['code'], 'PREVIOUS_DAY_DISH_CONFLICT')
        self.assertEqual(self.client.get('/meal-plans', params={'date': '2026-08-20'}).json()['meal_plans'], [])

    def test_known_capacity_over_budget_cannot_be_hidden_by_unknown_amount(self):
        res = self.priced_adopt(preview_with((None, '1001'), (None, None)))
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()['error']['code'], 'BUDGET_EXCEEDED')

    def test_capacity_does_not_bypass_nutrition_review(self):
        preview = {**preview_with((None, '100')), 'adoption_blocked': True}
        with patch.object(database_menu_plans, '_cost', side_effect=AssertionError('nutrition gate bypassed')):
            res = self.priced_adopt(preview)
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()['error']['code'], 'NOT_ADOPTABLE')
