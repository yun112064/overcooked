"""2026-11-03 採用／不採用資料庫菜單與已採用紀錄（參考 t11-t20-demo T16）。

T11 菜單與 T12 營養以替身回傳，測試的是採用交易本身：版本與菜單重驗、冪等、換一份、紀錄與趨勢。
"""
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from db_support import make_client, migrate_fresh, requires_db, seed_foods, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body

from app.services import meal_cost, recipe_retrieval


def _dish(recipe_id, name):
    return {'recipe_id': recipe_id, 'recipe_name': name, 'standard_dish_id': f'DISH-{recipe_id}', 'category': '主菜',
            'aliases': [], 'food_ids': ['FOOD000039'], 'specified': False}


COMBOS = [[_dish('R1', '蕃茄炒蛋'), _dish('R2', '炒青菜')], [_dish('R3', '紅燒肉'), _dish('R4', '冬瓜湯')]]
PASSED = {'status': 'nutrition_passed', 'status_message': '營養計算通過', 'targets': {'energy_kcal': '600', 'protein_g': '30'},
          'nutrition': {'totals': {'energy_kcal': '590', 'protein_g': '31'}}, 'nutrition_all': {'totals': {}},
          'dishes': [{'recipe_id': 'R1', 'steps': ['炒'], 'adjusted': True,
                      'ingredients': [{'name': '食鹽', 'food_id': 'FOOD000691', 'grams': '5/2', 'display_amount': Decimal('0.50'),
                                       'display_unit': '小匙', 'display_text': None}]}],
          'staple': None, 'people': [], 'daily_reference_meta': None, 'dish_portions': None}


def _menu(*_args, **_kwargs):
    return recipe_retrieval.RetrievalResult(recipe_retrieval.DATABASE_MENU, selected=COMBOS[0], candidates=[],
                                            previous_day=None, alternatives=COMBOS)


class _MealPlanApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()
        self.assertEqual(self.client.post('/auth/register', json={'email': 'plans@example.test',
                                                                  'password': PASSWORD}).status_code, 201)
        self.assertEqual(self.client.post('/household', json={'name': '測試家庭'}).status_code, 201)
        member = self.client.post('/members', json=member_body('成員A')).json()
        self.member = member
        res = self.client.post('/meal-requests', json=full_draft([{'member_id': member['id']}]))
        self.assertEqual(res.status_code, 201, res.text)
        self.request = res.json()
        patcher = patch('app.services.meal_plans.recipe_retrieval.retrieve_database_menu', side_effect=_menu)
        patcher.start()
        self.addCleanup(patcher.stop)

    def menu(self):
        res = self.client.get(f"/meal-requests/{self.request['id']}/recipes")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def adopt(self, recipe_ids, key='adopt-key-0001', preview=PASSED, request=None):
        request = request or self.request
        with patch('app.services.meal_plans.t12_planning.preview', return_value=preview):
            return self.client.post('/meal-plans', headers={'Idempotency-Key': key},
                                    json={'meal_request_id': request['id'], 'request_version': request['version'],
                                          'recipe_ids': recipe_ids})

    def adopt_yesterday(self, recipe_ids):
        """8/19 先採用一餐（8/20 問卷的前一日）。"""
        res = self.client.post('/meal-requests', json=full_draft([{'member_id': self.member['id']}],
                                                                 meal_date='2026-08-19'))
        self.assertEqual(res.status_code, 201, res.text)
        yesterday = res.json()
        if recipe_ids != ['R1', 'R2']:
            self.client.post(f"/meal-requests/{yesterday['id']}/menu-rejections",
                             json={'version': yesterday['version'], 'recipe_ids': ['R1', 'R2']})
        adopted = self.adopt(recipe_ids, key='yesterday-key-0001', request=yesterday)
        self.assertEqual(adopted.status_code, 201, adopted.text)


@requires_db
class MealPlanApiTests(_MealPlanApiBase):
    def test_not_adopting_moves_to_the_next_menu_until_none_are_left(self):
        self.assertEqual([d['recipe_id'] for d in self.menu()['selected']], ['R1', 'R2'])
        res = self.client.post(f"/meal-requests/{self.request['id']}/menu-rejections",
                               json={'version': self.request['version'], 'recipe_ids': ['R1', 'R2']})
        self.assertEqual(res.status_code, 204, res.text)
        menu = self.menu()
        self.assertEqual([d['recipe_id'] for d in menu['selected']], ['R3', 'R4'])
        self.assertEqual(menu['rejected_count'], 1)
        self.client.post(f"/meal-requests/{self.request['id']}/menu-rejections",
                         json={'version': self.request['version'], 'recipe_ids': ['R4', 'R3']})
        menu = self.menu()
        self.assertEqual(menu['selected'], [])
        self.assertEqual(menu['reason'], 'ALL_MENUS_REJECTED')

    def test_adopting_saves_a_snapshot_once_and_marks_the_questionnaire(self):
        res = self.adopt(['R2', 'R1'])
        self.assertEqual(res.status_code, 201, res.text)
        plan = res.json()['meal_plan']
        self.assertEqual([d['dish_name'] for d in plan['dishes']], ['蕃茄炒蛋', '炒青菜'])
        again = self.adopt(['R1', 'R2'])  # 同一把冪等鍵重送
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['meal_plan']['id'], plan['id'])
        self.assertEqual(self.adopt(['R1', 'R2'], key='another-key-0001').json()['error']['code'], 'ALREADY_ADOPTED')
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()['status'], 'confirmed')
        self.assertEqual(self.menu()['adopted_plan']['id'], plan['id'])
        detail = self.client.get(f"/meal-plans/{plan['id']}").json()
        self.assertEqual(detail['snapshot']['targets'], {'energy_kcal': '600', 'protein_g': '30'})
        # 精確值保存成字串（不轉 float）
        self.assertEqual(detail['snapshot']['dishes'][0]['ingredients'][0]['display_amount'], '0.50')

    def test_adoption_is_refused_when_the_menu_changed_or_nutrition_failed(self):
        self.assertEqual(self.adopt(['R3', 'R4']).json()['error']['code'], 'MENU_CHANGED')
        failed = {**PASSED, 'status': 'outside_tolerance'}
        self.assertEqual(self.adopt(['R1', 'R2'], key='adopt-key-0002', preview=failed).json()['error']['code'],
                         'NOT_ADOPTABLE')

    def test_history_lists_dates_plans_and_energy_trend(self):
        self.assertEqual(self.adopt(['R1', 'R2']).status_code, 201)
        meal_date = self.request['draft']['meal_date']
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [{'meal_date': meal_date, 'count': 1}])
        plans = self.client.get('/meal-plans', params={'date': meal_date}).json()['meal_plans']
        self.assertEqual(len(plans), 1)
        trend = self.client.get('/meal-plans/trend', params={'days': 7, 'until': meal_date}).json()
        self.assertEqual(trend['items'][0]['energy_kcal'], 590)
        self.assertEqual(trend['items'][0]['target_energy_kcal'], 600)
        self.assertLessEqual(date.fromisoformat(trend['since']), date.fromisoformat(meal_date))

    def test_preview_cannot_adopt_while_its_policy_or_mapping_is_blocked(self):
        blocked = {**PASSED, 'adoption_blocked': True}
        response = self.adopt(['R1', 'R2'], preview=blocked)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'NOT_ADOPTABLE')
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [])

    def test_preview_idempotency_key_cannot_be_reused_for_different_recipes(self):
        self.assertEqual(self.adopt(['R1', 'R2']).status_code, 201)
        response = self.adopt(['R3', 'R4'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'IDEMPOTENCY_KEY_REUSED')


def _with_ingredient(food_id, grams):
    dish = {**PASSED['dishes'][0], 'ingredients': [{'name': '高麗菜', 'food_id': food_id, 'grams': grams,
                                                     'display_amount': None, 'display_unit': None,
                                                     'display_text': None}]}
    return {**PASSED, 'dishes': [dish]}


def _fresh_quote(food_id, price, qty):
    from test_t13_meal_cost import quote
    return quote(food_id, price, qty, queried_at=datetime.now(timezone.utc) - timedelta(hours=1))


CABBAGE = 'FOOD000867'


@requires_db
class PreviewAdoptionT13AndHistoryTests(_MealPlanApiBase):
    """2026-11-08 修正：食譜庫預覽採用路徑補上 T13 成本／超預算，以及與生成結果採用相同的昨日重驗與快照。"""

    def test_preview_adoption_refuses_known_cost_over_budget(self):
        # 1000 g × 400 元 / 300 g ≈ 1333 元，超過問卷預算 300 元
        with patch('app.services.meal_cost.latest_quotes', return_value={CABBAGE: _fresh_quote(CABBAGE, '400', '300')}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '1000'))
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()['error']['code'], 'BUDGET_EXCEEDED')
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [])
        self.assertNotEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()['status'], 'confirmed')

    def test_preview_adoption_saves_cost_within_budget(self):
        with patch('app.services.meal_cost.latest_quotes', return_value={CABBAGE: _fresh_quote(CABBAGE, '30', '300')}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '300'))
        self.assertEqual(res.status_code, 201, res.text)
        cost = self.client.get(f"/meal-plans/{res.json()['meal_plan']['id']}").json()['snapshot']['cost']
        self.assertEqual(cost['status'], 'computed')
        self.assertEqual(cost['budget_status'], meal_cost.BUDGET_OK)
        self.assertEqual(Decimal(cost['known_cost_total']), Decimal('30'))

    def test_preview_adoption_with_missing_price_is_adoptable_but_flagged(self):
        with patch('app.services.meal_cost.latest_quotes', return_value={}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '300'))
        self.assertEqual(res.status_code, 201, res.text)
        snapshot = self.client.get(f"/meal-plans/{res.json()['meal_plan']['id']}").json()['snapshot']
        self.assertEqual(snapshot['cost']['budget_status'], 'PRICE_INCOMPLETE')
        self.assertEqual(snapshot['missing']['price_food_ids'], [CABBAGE])
        self.assertTrue(snapshot['missing']['price_exception_accepted'])

    def test_preview_adoption_without_final_quantities_is_refused(self):
        unquantified = _with_ingredient(CABBAGE, None)
        res = self.adopt(['R1', 'R2'], preview=unquantified)
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()['error']['code'], 'PRICE_INCOMPLETE')

    def test_preview_adoption_rechecks_previous_day_history(self):
        # 8/19 已採用 R1、R2；8/20 的過時菜單仍是同一組 → 採用前重讀昨日紀錄而拒絕
        self.adopt_yesterday(['R1', 'R2'])
        res = self.adopt(['R1', 'R2'])
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()['error']['code'], 'PREVIOUS_DAY_DISH_CONFLICT')
        self.assertEqual(sorted(res.json()['error']['details']['conflicting_dishes']), ['炒青菜', '蕃茄炒蛋'])
        self.assertEqual(self.client.get('/meal-plans', params={'date': '2026-08-20'}).json()['meal_plans'], [])

    def test_preview_snapshot_keeps_previous_day_sources_and_rule_versions(self):
        self.adopt_yesterday(['R3', 'R4'])
        res = self.adopt(['R1', 'R2'])
        self.assertEqual(res.status_code, 201, res.text)
        snapshot = self.client.get(f"/meal-plans/{res.json()['meal_plan']['id']}").json()['snapshot']
        previous = snapshot['previous_day']['at_adoption']
        self.assertEqual(previous['previous_date'], '2026-08-19')
        self.assertEqual(sorted(d['standard_dish_id'] for d in previous['excluded_dishes']), ['DISH-R3', 'DISH-R4'])
        self.assertFalse(snapshot['previous_day']['history_changed'])
        self.assertIn('recipe_sources', snapshot)
        self.assertEqual(snapshot['adopted_versions'], {'meal_request_id': self.request['id'],
                                                        'request_version': self.request['version'],
                                                        'recipe_ids': ['R1', 'R2']})
        self.assertEqual(snapshot['request']['meal_date'], '2026-08-20')
        versions = snapshot['versions']
        self.assertEqual(versions['adoption_rule_version'], 't16-adoption/v2')
        self.assertEqual(versions['snapshot_schema_version'], 'database_menu_snapshot/v2')
        self.assertTrue(versions['nutrition_rule_version'])
        self.assertEqual(snapshot['eligibility']['blocks'], [])
