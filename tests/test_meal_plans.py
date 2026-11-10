"""2026-11-03 採用／不採用資料庫菜單與已採用紀錄（參考 t11-t20-demo T16）。

T11 菜單與 T12 營養以替身回傳，測試的是採用交易本身：版本與菜單重驗、冪等、換一份、紀錄與趨勢。
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import sqlalchemy as sa

from db_support import make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body

from app.db.models import MealConfirmationEvent
from app.services import recipe_retrieval


def _dish(recipe_id, name):
    return {'recipe_id': recipe_id, 'recipe_name': name, 'standard_dish_id': f'DISH-{recipe_id}', 'category': '主菜',
            'aliases': [], 'food_ids': ['FOOD000039'], 'specified': False}


COMBOS = [[_dish('R1', '蕃茄炒蛋'), _dish('R2', '炒青菜')], [_dish('R3', '紅燒肉'), _dish('R4', '冬瓜湯')]]
PASSED = {'status': 'nutrition_passed', 'status_message': '營養計算通過', 'targets': {'energy_kcal': '600', 'protein_g': '30'},
          'nutrition': {'totals': {'energy_kcal': '590', 'protein_g': '31'}}, 'nutrition_all': {'totals': {}},
          'dishes': [{'recipe_id': 'R1', 'steps': ['炒'], 'adjusted': True,
                      'ingredients': [{'name': '食鹽', 'grams': '5/2', 'display_amount': Decimal('0.50'),
                                       'display_unit': '小匙', 'display_text': None}]}],
          'staple': None, 'people': [], 'daily_reference_meta': None, 'dish_portions': None}


def _menu(*_args, **_kwargs):
    return recipe_retrieval.RetrievalResult(recipe_retrieval.DATABASE_MENU, selected=COMBOS[0], candidates=[],
                                            previous_day=None, alternatives=COMBOS)


@requires_db
class MealPlanApiTests(unittest.TestCase):
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

    def adopt(self, recipe_ids, key='adopt-key-0001', preview=PASSED):
        with patch('app.services.meal_plans.t12_planning.preview', return_value=preview):
            return self.client.post('/meal-plans', headers={'Idempotency-Key': key},
                                    json={'meal_request_id': self.request['id'], 'request_version': self.request['version'],
                                          'recipe_ids': recipe_ids})

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
        with session_factory()() as db:
            self.assertEqual(db.scalars(sa.select(MealConfirmationEvent.stage)
                                        .order_by(MealConfirmationEvent.sequence)).all(), [
                'confirmed', 'snapshot_saved', 'history_saved', 'ingredients_saved', 'nutrition_saved',
            ])

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
