"""2026-11-03 採用／不採用資料庫菜單與已採用紀錄（移植自主線；demo 版 T12 預覽與 T13 成本）。

T11 菜單與 T12 營養以替身回傳，測試的是採用交易本身：版本與菜單重驗、冪等、換一份、紀錄與趨勢。
需要 PostgreSQL 的類別標 @requires_db；主食防線另有不連資料庫的採用測試（AdoptGuardUnitTests）。
"""
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

# 設定物件要求 DATABASE_URL；這些測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from db_support import make_client, migrate_fresh, requires_db, seed_foods, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body

from app.api.errors import ApiError
from app.meal_calc.types import IngredientRequirement
from app.services import database_menu_plans, meal_cost, recipe_retrieval, t12_planning
from app.services.meal_calculation import IngredientLine, IngredientNutrition
from app.services.meal_request import MealRequestDraft

RICE = "FOOD000296"  # config/staples.json 白飯
CABBAGE = 'FOOD000867'


def _dish(recipe_id, name):
    return {'recipe_id': recipe_id, 'recipe_name': name, 'standard_dish_id': f'DISH-{recipe_id}', 'category': '主菜',
            'aliases': [], 'food_ids': ['FOOD000039'], 'specified': False}


COMBOS = [[_dish('R1', '蕃茄炒蛋'), _dish('R2', '炒青菜')], [_dish('R3', '紅燒肉'), _dish('R4', '冬瓜湯')]]


def _calc(requirements=(), unresolved=()):
    """t12_planning.preview 的 _calc：採用時 T13 依 requirements 計算成本。"""
    return {'requirements': list(requirements), 'unresolved_ingredients': list(unresolved)}


PASSED = {'status': 'nutrition_passed', 'status_message': '', 'nutrition_passed': True, 'adoption_blocked': False,
          'targets': {'energy_kcal': '600', 'protein_g': '30'},
          'nutrition': {'totals': {'energy_kcal': '590', 'protein_g': '31'}, 'missing': []},
          'nutrition_all': {'totals': {}},
          'dishes': [{'recipe_id': 'R1', 'steps': ['炒'], 'adjusted': True,
                      'ingredients': [{'name': '食鹽', 'food_id': 'FOOD000691', 'grams': '2.5', 'display_amount': '0.50',
                                       'display_unit': '小匙', 'display_text': None, 'group': '調味'}]}],
          'staple': None, 'people': [], 'daily_reference_meta': None, 'dish_portions': None,
          '_calc': _calc([IngredientRequirement('FOOD000691', Decimal('2.5'))])}


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
            return self.adopt_unpatched(recipe_ids, key=key, request=request)

    def adopt_unpatched(self, recipe_ids, key='adopt-key-0001', request=None):
        request = request or self.request
        return self.client.post('/meal-plans', headers={'Idempotency-Key': key},
                                json={'meal_request_id': request['id'], 'request_version': request['version'],
                                      'recipe_ids': recipe_ids})

    def reject(self, request, recipe_ids):
        """「不採用，換一份」：直接寫入（路由由後續套件加上）。"""
        from db_support import session_factory
        from app.db.models import MealRequest
        with session_factory()() as db:
            row = db.get(MealRequest, uuid.UUID(request['id']))
            database_menu_plans.reject(db, row, version=request['version'], recipe_ids=recipe_ids, user_id=None)
            db.commit()

    def adopt_yesterday(self, recipe_ids):
        """8/19 先採用一餐（8/20 問卷的前一日）。"""
        res = self.client.post('/meal-requests', json=full_draft([{'member_id': self.member['id']}],
                                                                 meal_date='2026-08-19'))
        self.assertEqual(res.status_code, 201, res.text)
        yesterday = res.json()
        if recipe_ids != ['R1', 'R2']:
            self.reject(yesterday, ['R1', 'R2'])
        adopted = self.adopt(recipe_ids, key='yesterday-key-0001', request=yesterday)
        self.assertEqual(adopted.status_code, 201, adopted.text)


@requires_db
class MealPlanApiTests(_MealPlanApiBase):
    def test_not_adopting_moves_to_the_next_menu_until_none_are_left(self):
        self.assertEqual([d['recipe_id'] for d in self.menu()['selected']], ['R1', 'R2'])
        self.reject(self.request, ['R1', 'R2'])
        menu = self.menu()
        self.assertEqual([d['recipe_id'] for d in menu['selected']], ['R3', 'R4'])
        self.assertEqual(menu['rejected_count'], 1)
        self.reject(self.request, ['R4', 'R3'])
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
        self.assertTrue(detail['snapshot_available'])
        self.assertEqual(detail['snapshot']['targets'], {'energy_kcal': '600', 'protein_g': '30'})
        # 精確值保存成字串（不轉 float）
        self.assertEqual(detail['snapshot']['dishes'][0]['ingredients'][0]['display_amount'], '0.50')
        self.assertNotIn('_calc', detail['snapshot'])
        self.assertIsNone(detail['snapshot']['versions']['recipe_release_id'])
        listed = self.client.get('/meal-plans', params={'date': self.request['draft']['meal_date']}).json()
        [row] = listed['meal_plans']
        self.assertEqual((row['energy_kcal'], row['protein_g'], row['status']), ('590', '31', 'adopted'))
        self.assertIsNone(row['result_version'])

    def test_adoption_is_refused_when_the_menu_changed_or_nutrition_failed(self):
        self.assertEqual(self.adopt(['R3', 'R4']).json()['error']['code'], 'MENU_CHANGED')
        failed = {**PASSED, 'status': 'outside_tolerance', 'adoption_blocked': True}
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

    def test_staple_with_excluded_food_cannot_be_adopted(self):
        """真實 t12_planning.preview（食譜與營養以替身提供）：主食白飯被列為禁吃 → 409 NOT_ADOPTABLE。"""
        with _staple_conflict_patches():
            response = self.adopt_unpatched(['R1', 'R2'])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'NOT_ADOPTABLE')
        self.assertIn('白飯', response.json()['error']['message'])
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [])


def _with_ingredient(food_id, grams):
    dish = {**PASSED['dishes'][0], 'ingredients': [{'name': '高麗菜', 'food_id': food_id, 'grams': grams,
                                                     'display_amount': None, 'display_unit': None,
                                                     'display_text': None}]}
    calc = (_calc([IngredientRequirement(food_id, Decimal(grams))]) if grams is not None
            else _calc(unresolved=[{'food_id': food_id, 'name': '高麗菜', 'reason': 'weight_unresolved'}]))
    return {**PASSED, 'dishes': [dish], '_calc': calc}


def _fresh_quote(food_id, price, qty):
    from test_t13_meal_cost import quote
    return quote(food_id, price, qty, queried_at=datetime.now(timezone.utc) - timedelta(hours=1))


def _quotes(table):
    """DEMO T13 的查價接縫：meal_cost.latest_quote(db, food_id, now=)。"""
    return patch('app.services.meal_cost.latest_quote', side_effect=lambda _db, food_id, now=None: table.get(food_id))


@requires_db
class PreviewAdoptionT13AndHistoryTests(_MealPlanApiBase):
    """2026-11-08 修正：食譜庫預覽採用路徑補上 T13 成本／超預算，以及與生成結果採用相同的昨日重驗與快照。"""

    def test_preview_adoption_refuses_known_cost_over_budget(self):
        # 1000 g × 400 元 / 300 g ≈ 1333 元，超過問卷預算 300 元
        with _quotes({CABBAGE: _fresh_quote(CABBAGE, '400', '300')}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '1000'))
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()['error']['code'], 'BUDGET_EXCEEDED')
        self.assertEqual(self.client.get('/meal-plans').json()['dates'], [])
        self.assertNotEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()['status'], 'confirmed')

    def test_preview_adoption_saves_cost_within_budget(self):
        with _quotes({CABBAGE: _fresh_quote(CABBAGE, '30', '300')}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '300'))
        self.assertEqual(res.status_code, 201, res.text)
        cost = self.client.get(f"/meal-plans/{res.json()['meal_plan']['id']}").json()['snapshot']['cost']
        self.assertEqual(cost['status'], 'computed')
        self.assertEqual(cost['budget_status'], meal_cost.BUDGET_OK)
        self.assertEqual(Decimal(cost['known_cost_total']), Decimal('30'))
        self.assertEqual(cost['unquantified_food_ids'], [])

    def test_preview_adoption_with_missing_price_is_adoptable_but_flagged(self):
        with _quotes({}):
            res = self.adopt(['R1', 'R2'], preview=_with_ingredient(CABBAGE, '300'))
        self.assertEqual(res.status_code, 201, res.text)
        snapshot = self.client.get(f"/meal-plans/{res.json()['meal_plan']['id']}").json()['snapshot']
        self.assertEqual(snapshot['cost']['budget_status'], 'PRICE_INCOMPLETE')
        self.assertIsNone(snapshot['cost']['cost_total'])  # 缺價不是 0
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


# ---- 主食防線：不連資料庫也會執行 ----

def _staple_conflict_patches():
    """真實 preview，只替換食譜載入、calculator 與限制：白飯（主食）被列為禁吃。"""
    from contextlib import ExitStack

    def calculator(rows):
        return [IngredientNutrition({'energy_kcal': Decimal(2) * g, 'protein_g': Decimal('0.1') * g}, {}, None)
                for _ln, g in rows]

    def database_dishes(_db, summaries):
        dishes, lines = [], []
        for s in summaries:
            ln = IngredientLine(food_id=CABBAGE, name='高麗菜', grams_per_n=Decimal(100), dish_id=s['recipe_id'],
                                unit='g')
            lines.append(ln)
            dishes.append({'recipe_id': s['recipe_id'], 'dish_name': s['recipe_name'], 'steps': [],
                           'food_ids': [CABBAGE]})
        return dishes, lines

    summaries = [{'recipe_id': d['recipe_id'], 'recipe_name': d['recipe_name'], 'standard_dish_id': d['standard_dish_id'],
                  'aliases': [], 'specified': False, 'category': '主菜'} for d in COMBOS[0]]
    stack = ExitStack()
    stack.enter_context(patch.object(t12_planning, '_summaries', return_value=summaries))
    stack.enter_context(patch.object(t12_planning, '_calculator', return_value=calculator))
    stack.enter_context(patch.object(t12_planning.generation, '_database_dishes', side_effect=database_dishes))
    stack.enter_context(patch.object(t12_planning.meal_request, 'restrictions',
                                     return_value={'excluded_food_ids': [RICE]}))
    return stack


class _FakeSession:
    def execute(self, *_args, **_kwargs):
        return None

    def scalar(self, *_args, **_kwargs):
        return None

    def get_bind(self):
        return 'fake'


class AdoptGuardUnitTests(unittest.TestCase):
    def test_staple_conflict_is_refused_with_not_adoptable(self):
        members = {uuid.UUID(int=1): SimpleNamespace(display_name='成員A')}
        draft = MealRequestDraft(meal_date=date(2026, 8, 20), meal_type='dinner', budget=Decimal(300),
                                 staple='白飯', dish_count=2)
        row = SimpleNamespace(id=uuid.uuid4(), version=1, status='draft', payload={})
        info = {'status': 'available', 'total_n': '1', 'target': {'energy_kcal': '600', 'protein_g': '30'},
                'items': [{'participation_id': f'{uuid.UUID(int=1)}:dining', 'kind': 'dining', 'meal': 'dinner',
                           'meal_ratio': '0.4', 'n': '1', 'scaled_target': {'energy_kcal': '600', 'protein_g': '30'}}]}
        with _staple_conflict_patches(), \
                patch.object(t12_planning.meal_request, 'target_preview', return_value=info), \
                patch.object(database_menu_plans, 'current_menu', return_value={'selected': COMBOS[0]}):
            with self.assertRaises(ApiError) as caught:
                database_menu_plans.adopt(_FakeSession(), household_id=uuid.uuid4(), user_id=None, row=row,
                                          draft=draft, members=members, version=1, recipe_ids=['R1', 'R2'],
                                          idempotency_key='adopt-key-0001')
        self.assertEqual((caught.exception.status_code, caught.exception.code), (409, 'NOT_ADOPTABLE'))
        self.assertIn('白飯', caught.exception.message)

    def test_cost_uses_calc_requirements_and_keeps_unquantified_unknown(self):
        preview = {**PASSED, 'staple': {'food_id': RICE, 'name': '白飯', 'final_grams': None},
                   '_calc': _calc([IngredientRequirement(CABBAGE, Decimal(300))],
                                  unresolved=[{'food_id': 'FOOD000039', 'name': '大蒜'},
                                              {'food_id': RICE, 'name': '白飯'}])}
        draft = MealRequestDraft(budget=Decimal(300))
        with _quotes({CABBAGE: _fresh_quote(CABBAGE, '30', '300')}):
            cost = database_menu_plans._cost(object(), preview, draft)
        self.assertEqual(cost['status'], 'computed')
        self.assertEqual(Decimal(cost['known_cost_total']), Decimal(30))
        self.assertEqual(cost['unquantified_food_ids'], ['FOOD000039'])  # 主食不計價（決策 #16）
        empty = database_menu_plans._cost(object(), {**preview, '_calc': _calc()}, draft)
        self.assertEqual((empty['status'], empty['budget_status']), ('not_computed', None))

    def test_market_shortages_skip_free_foods_and_keep_unknown_grams_unquantified(self):
        preview = {'dishes': [{'ingredients': [
            {'food_id': CABBAGE, 'name': '高麗菜', 'grams': '300'},
            {'food_id': CABBAGE, 'name': '高麗菜', 'grams': '50.5'},
            {'food_id': 'FOOD000039', 'name': '大蒜', 'grams': None},
            {'food_id': 'FOOD000691', 'name': '食鹽', 'grams': '2.5'}]}],
            'staple': {'food_id': RICE, 'name': '白飯', 'final_grams': '150'}}
        shortages = database_menu_plans._market_shortages(preview, [])
        self.assertEqual(shortages['items'], [{'food_id': CABBAGE, 'name': '高麗菜', 'shortage_grams': '350.5'},
                                              {'food_id': RICE, 'name': '白飯', 'shortage_grams': '150'}])
        self.assertEqual(shortages['unquantified_items'], [{'food_id': 'FOOD000039', 'name': '大蒜'}])
        self.assertIsNone(shortages['recipe_release_id'])
        owned = database_menu_plans._market_shortages(preview, [CABBAGE])
        self.assertNotIn(CABBAGE, [i['food_id'] for i in owned['items']])


if __name__ == '__main__':
    unittest.main()
