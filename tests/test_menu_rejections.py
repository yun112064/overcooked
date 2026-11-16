"""「不採用，換一份」（POST /meal-requests/{id}/menu-rejections，2026-11-03 移植自主線）。

不連資料庫的單元測試：以假 session 與替身 T11 菜單驗證 database_menu_plans.reject／current_menu；
API 類別需要 PostgreSQL，標 @requires_db（CI 才會執行）。
"""
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

# 設定物件要求 DATABASE_URL；單元測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from db_support import make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body

from app.api.errors import ApiError
from app.db.models import MealMenuRejection, MealRequest
from app.services import database_menu_plans, recipe_retrieval


def _dish(recipe_id, name):
    return {'recipe_id': recipe_id, 'recipe_name': name, 'standard_dish_id': f'DISH-{recipe_id}', 'category': '主菜',
            'aliases': [], 'food_ids': ['FOOD000039'], 'specified': False}


COMBOS = [[_dish('R1', '蕃茄炒蛋'), _dish('R2', '炒青菜')], [_dish('R3', '紅燒肉'), _dish('R4', '冬瓜湯')]]


def _menu(*_args, **_kwargs):
    return recipe_retrieval.RetrievalResult(recipe_retrieval.DATABASE_MENU, selected=COMBOS[0], candidates=[],
                                            previous_day=None, alternatives=COMBOS)


class _FakeSession:
    """只支援 reject／current_menu 用到的 add、flush 與 scalars(select(MealMenuRejection)...)。"""

    def __init__(self):
        self.added = []
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushed += 1

    def scalars(self, statement):
        params = statement.compile().params
        request_id = next(v for k, v in params.items() if k.startswith('meal_request_id'))
        version = next(v for k, v in params.items() if k.startswith('request_version'))
        return [r for r in self.added if isinstance(r, MealMenuRejection)
                and r.meal_request_id == request_id and r.request_version == version]


class RejectionUnitTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeSession()
        self.row = SimpleNamespace(id=uuid.uuid4(), version=1, status='draft')
        patcher = patch.object(recipe_retrieval, 'retrieve_database_menu', side_effect=_menu)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(database_menu_plans.request_service, 'restrictions',
                               return_value={'excluded_food_ids': []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def menu(self):
        return database_menu_plans.current_menu(self.db, uuid.uuid4(), self.row, draft=None, members={})

    def reject(self, recipe_ids, version=None):
        database_menu_plans.reject(self.db, self.row, version=self.row.version if version is None else version,
                                   recipe_ids=recipe_ids, user_id=None)

    def test_first_rejection_moves_to_the_second_menu_and_the_second_exhausts_them(self):
        first = self.menu()
        self.assertEqual([d['recipe_id'] for d in first['selected']], ['R1', 'R2'])
        self.assertEqual(first['rejected_count'], 0)
        self.reject(['R2', 'R1'])
        second = self.menu()
        self.assertEqual(second['status'], recipe_retrieval.DATABASE_MENU)
        self.assertEqual([d['recipe_id'] for d in second['selected']], ['R3', 'R4'])
        self.assertEqual(second['rejected_count'], 1)
        self.reject(['R4', 'R3'])
        last = self.menu()
        self.assertEqual(last['selected'], [])
        self.assertEqual(last['alternatives'], [])
        self.assertEqual(last['status'], database_menu_plans.ALL_REJECTED)
        self.assertEqual(last['reason'], 'ALL_MENUS_REJECTED')
        self.assertEqual(last['rejected_count'], 2)

    def test_recipe_ids_are_stored_sorted_and_unique_on_the_current_version(self):
        self.reject(['R2', 'R1', 'R2'])
        saved = self.db.added[-1]
        self.assertEqual(saved.recipe_ids, ['R1', 'R2'])
        self.assertEqual(saved.request_version, 1)
        self.assertEqual(saved.meal_request_id, self.row.id)
        self.assertEqual(self.db.flushed, 1)

    def test_stale_version_is_a_conflict_and_nothing_is_saved(self):
        self.row.version = 2
        with self.assertRaises(ApiError) as caught:
            self.reject(['R1', 'R2'], version=1)
        self.assertEqual((caught.exception.status_code, caught.exception.code), (409, 'VERSION_CONFLICT'))
        self.assertEqual(self.db.added, [])

    def test_confirmed_questionnaire_cannot_reject(self):
        self.row.status = 'confirmed'
        with self.assertRaises(ApiError) as caught:
            self.reject(['R1', 'R2'])
        self.assertEqual((caught.exception.status_code, caught.exception.code), (409, 'ALREADY_ADOPTED'))
        self.assertEqual(self.db.added, [])

    def test_editing_the_questionnaire_resets_rejections(self):
        self.reject(['R1', 'R2'])
        self.row.version = 2  # 問卷修改後升版：舊版本的不採用不再套用
        menu = self.menu()
        self.assertEqual([d['recipe_id'] for d in menu['selected']], ['R1', 'R2'])
        self.assertEqual(menu['rejected_count'], 0)


@requires_db
class MenuRejectionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()
        self.assertEqual(self.client.post('/auth/register', json={'email': 'reject@example.test',
                                                                  'password': PASSWORD}).status_code, 201)
        self.assertEqual(self.client.post('/household', json={'name': '測試家庭'}).status_code, 201)
        member = self.client.post('/members', json=member_body('成員A')).json()
        res = self.client.post('/meal-requests', json=full_draft([{'member_id': member['id']}]))
        self.assertEqual(res.status_code, 201, res.text)
        self.request = res.json()
        patcher = patch.object(recipe_retrieval, 'retrieve_database_menu', side_effect=_menu)
        patcher.start()
        self.addCleanup(patcher.stop)

    def menu(self):
        res = self.client.get(f"/meal-requests/{self.request['id']}/recipes")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def reject(self, recipe_ids, version=None):
        return self.client.post(f"/meal-requests/{self.request['id']}/menu-rejections",
                                json={'version': version or self.request['version'], 'recipe_ids': recipe_ids})

    def test_not_adopting_moves_to_the_next_menu_until_none_are_left(self):
        self.assertEqual([d['recipe_id'] for d in self.menu()['selected']], ['R1', 'R2'])
        res = self.reject(['R1', 'R2'])
        self.assertEqual(res.status_code, 204, res.text)
        menu = self.menu()
        self.assertEqual([d['recipe_id'] for d in menu['selected']], ['R3', 'R4'])
        self.assertEqual(menu['rejected_count'], 1)
        self.assertEqual(self.reject(['R4', 'R3']).status_code, 204)
        menu = self.menu()
        self.assertEqual(menu['selected'], [])
        self.assertEqual(menu['reason'], 'ALL_MENUS_REJECTED')
        self.assertEqual(menu['rejected_count'], 2)

    def test_stale_version_and_adopted_questionnaire_are_refused(self):
        res = self.reject(['R1', 'R2'], version=self.request['version'] + 1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['error']['code'], 'VERSION_CONFLICT')
        with session_factory()() as db:
            db.get(MealRequest, uuid.UUID(self.request['id'])).status = 'confirmed'
            db.commit()
        res = self.reject(['R1', 'R2'])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['error']['code'], 'ALREADY_ADOPTED')

    def test_other_household_cannot_reject(self):
        other = make_client()
        self.assertEqual(other.post('/auth/register', json={'email': 'other@example.test',
                                                            'password': PASSWORD}).status_code, 201)
        self.assertEqual(other.post('/household', json={'name': '別的家庭'}).status_code, 201)
        res = other.post(f"/meal-requests/{self.request['id']}/menu-rejections",
                         json={'version': self.request['version'], 'recipe_ids': ['R1', 'R2']})
        self.assertEqual(res.status_code, 404)


if __name__ == '__main__':
    unittest.main()
