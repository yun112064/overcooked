"""T11 雲端營養小知識：T12 之後的即時 API 與服務（資料庫測試沿用 T08 的合成家庭；服務測試不連網）。

本分支的 grounded_in_nutrition 仍用 v1 關鍵字（config/nutrition_keywords.json），提示詞驗證用 v2
（config/nutrition_explain_keywords.json）；只出現 v2 新增關鍵字的小知識一律不顯示（fail-closed）。
"""
import json
import os
import unittest
from unittest.mock import patch

import pytest

# 設定物件要求 DATABASE_URL；服務層測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from db_support import seed_foods, make_client, migrate_fresh, requires_db, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body


@requires_db
class KnowledgeTipsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()
        self.assertEqual(self.client.post("/auth/register", json={"email": "tips@example.test",
                                                                  "password": PASSWORD}).status_code, 201)
        self.assertEqual(self.client.post("/household", json={"name": "測試家庭"}).status_code, 201)
        self.member = self.client.post("/members", json=member_body("成員A")).json()

    def create(self, draft):
        res = self.client.post("/meal-requests", json=draft)
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def test_uses_server_side_t12_food_ids_not_client_input(self):
        created = self.create(full_draft([{"member_id": self.member["id"]}]))
        menu = {"status": "nutrition_passed", "adoption_blocked": False,
                "final_food_ids": ["FOOD000039", "FOOD001566"]}
        tips = {"status": "available", "items": [{"ingredient_id": "FOOD000039", "tip_zh": "示範"}],
                "dropped": [], "not_found": ["FOOD001566"], "final_food_ids": ["FOOD000039", "FOOD001566"],
                "knowledge_status": "available", "index_version": "idx-1"}
        with patch("app.api.meal_requests.t12_planning.preview", return_value=menu) as preview, \
                patch("app.api.meal_requests.knowledge_explanation.tips_for_menu", return_value=tips) as run:
            res = self.client.post(f"/meal-requests/{created['id']}/knowledge-tips", json={"recipe_ids": ["R001"]})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["items"], tips["items"])
        self.assertEqual(body["nutrition_status"], "nutrition_passed")
        self.assertEqual(body["input_version"], created["version"])
        self.assertEqual(preview.call_args.kwargs["recipe_ids"], ["R001"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["final_food_ids"], ["FOOD000039", "FOOD001566"])
        self.assertTrue(kwargs["request_tag"].startswith(f"tips:{created['id']}:"))

    def test_missing_adoption_flag_is_treated_as_blocked(self):
        # 預覽少了 adoption_blocked 時寧可擋下，不花 T10 額度
        created = self.create(full_draft([{"member_id": self.member["id"]}]))
        menu = {"status": "nutrition_passed", "final_food_ids": ["FOOD000039"]}
        with patch("app.api.meal_requests.t12_planning.preview", return_value=menu),                 patch("app.api.meal_requests.knowledge_explanation.tips_for_menu") as run:
            res = self.client.post(f"/meal-requests/{created['id']}/knowledge-tips", json={"recipe_ids": ["R001"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["items"], [])
        run.assert_not_called()

    def test_incomplete_questionnaire_is_rejected_before_any_paid_call(self):
        created = self.create({"meal_type": "lunch"})
        with patch("app.api.meal_requests.knowledge_explanation.tips_for_menu") as run:
            res = self.client.post(f"/meal-requests/{created['id']}/knowledge-tips", json={"recipe_ids": ["R001"]})
        self.assertEqual(res.status_code, 422)
        run.assert_not_called()

    def test_t12_unavailable_is_reported(self):
        from app.services.t12_planning import T12PlanningUnavailable
        created = self.create(full_draft([{"member_id": self.member["id"]}]))
        with patch("app.api.meal_requests.t12_planning.preview",
                   side_effect=T12PlanningUnavailable("NUTRITION_DATA_UNAVAILABLE", "x")), \
                patch("app.api.meal_requests.knowledge_explanation.tips_for_menu") as run:
            res = self.client.post(f"/meal-requests/{created['id']}/knowledge-tips", json={"recipe_ids": ["R001"]})
        self.assertEqual(res.status_code, 503)
        run.assert_not_called()

    def test_blocked_preview_returns_unavailable_without_paid_call(self):
        created = self.create(full_draft([{"member_id": self.member["id"]}]))
        menu = {"status": "calculation_incomplete", "final_food_ids": [], "nutrition_passed": False}
        with patch("app.api.meal_requests.t12_planning.preview", return_value=menu), \
                patch("app.api.meal_requests.knowledge_explanation.tips_for_menu") as run:
            res = self.client.post(f"/meal-requests/{created['id']}/knowledge-tips", json={"recipe_ids": ["R001"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "unavailable")
        self.assertEqual(res.json()["reason"], "NUTRITION_NOT_PASSED")
        run.assert_not_called()


# --- 服務層（不需資料庫） ---

# 關鍵字（多酚、抗氧化、腸胃、血糖）同時在 v1 與 v2 清單，兩道驗證都適用
GARLIC_FACT = '大蒜含有多酚等抗氧化成分；一次吃太多可能造成腸胃不適。'
REC = dict(knowledge_id='K1', source_version='v', ingredient_id='FOOD000001', ingredient_name='大蒜',
           fact_zh=GARLIC_FACT, conditions='限一般食用量。',
           # Synthetic evidence fixture, not an approval of production source content.
           nutrition_fact=GARLIC_FACT, evidence_quote=GARLIC_FACT,
           evidence_type='食材直接衛教', source_url='https://example.test/a', status='approved',
           source_name='測試健康網', title='大蒜的營養', published_at='2026-10-01')


class FakeExplainer:
    model = 'gpt-5.4-mini-2026-03-17'

    def __init__(self, tips):
        self.tips = tips

    def respond(self, input_text):
        data = json.loads(input_text)
        tip = self.tips[data['ingredient_name']]
        return json.dumps({'tip_zh': tip, 'citations': [data['knowledge'][0]['knowledge_id']]},
                          ensure_ascii=False), {'input_tokens': 1, 'output_tokens': 1}


GARLIC_TIP = '大蒜含多酚等抗氧化成分，但吃太多可能腸胃不適。'


def calls_through(**kw):
    return kw['call']()[0]


def test_explain_snapshot_keeps_verified_and_drops_overclaim():
    ke = pytest.importorskip('app.services.knowledge_explanation')
    pepper = '規律吃辣椒，可能與飯後血糖較平穩有關。'
    rec2 = dict(REC, knowledge_id='K2', ingredient_id='FOOD000002', ingredient_name='辣椒',
                fact_zh=pepper, nutrition_fact=pepper, evidence_quote=pepper)
    snap = dict(status='available', records=[REC, rec2],
                by_ingredient={'FOOD000001': 'available', 'FOOD000002': 'available', 'FOOD000003': 'not_found'})
    out = ke.explain_snapshot(calls_through, FakeExplainer({'大蒜': GARLIC_TIP, '辣椒': '辣椒保證降血糖。'}), snap)
    assert [i['tip_zh'] for i in out['items']] == [GARLIC_TIP]
    assert out['items'][0]['citations'][0]['source_label'] == '測試健康網'
    assert out['dropped'][0]['ingredient_id'] == 'FOOD000002' and 'OVERCLAIM' in out['dropped'][0]['reason']
    assert out['not_found'] == ['FOOD000003'] and out['status'] == 'available'


def test_budget_exhausted_marks_unavailable_without_failing_menu():
    ke = pytest.importorskip('app.services.knowledge_explanation')
    from app.services import spend

    def broke(**kw):
        raise spend.BudgetExhausted()
    snap = dict(status='available', records=[REC], by_ingredient={'FOOD000001': 'available'})
    out = ke.explain_snapshot(broke, FakeExplainer({}), snap)
    assert out['status'] == 'unavailable' and out['dropped'] == [{'ingredient_id': 'FOOD000001',
                                                                  'reason': 'BUDGET_EXHAUSTED'}]


def test_missing_key_reports_not_configured():
    ke = pytest.importorskip('app.services.knowledge_explanation')
    snap = dict(status='available', records=[REC], by_ingredient={'FOOD000001': 'available'})
    assert ke.explain_snapshot(calls_through, None, snap)['reason'] == 'EXPLANATION_NOT_CONFIGURED'


@pytest.mark.parametrize('missing_field', ['nutrition_fact', 'evidence_quote'])
def test_missing_evidence_never_calls_paid_explainer(missing_field):
    from app.services import knowledge_explanation as ke
    record = {k: v for k, v in REC.items() if k != missing_field}

    def forbidden(**kwargs):
        pytest.fail('A legacy fact_zh alone must not reach a paid model call')

    snap = dict(status='available', records=[record], by_ingredient={'FOOD000001': 'available'})
    out = ke.explain_snapshot(forbidden, FakeExplainer({}), snap)
    assert out == {'status': 'not_found', 'items': [], 'dropped': [], 'not_found': ['FOOD000001']}


def test_request_paid_call_uses_unique_keys(monkeypatch):
    ke = pytest.importorskip('app.services.knowledge_explanation')
    keys = []
    monkeypatch.setattr(ke.spend, 'paid_call', lambda factory, **kw: keys.append(kw['call_key']))
    for _ in range(2):
        ke.request_paid_call(None, request_tag='tips:r:1')(name='explain:FOOD1', purpose='p', provider='openai',
                                                           model='m', max_input_tokens=1, max_output_tokens=1,
                                                           call=lambda: None)
    assert len(set(keys)) == 2 and all(k.startswith('tips:r:1:') and k.endswith(':explain:FOOD1') for k in keys)


def test_tip_grounded_only_in_v2_keyword_is_dropped():
    """「食慾」只在 v2 清單：提示詞驗證通過，但本分支的 v1 營養依據檢查不通過，不顯示。"""
    from app.services import knowledge_explanation as ke
    fact = '食用大蒜有助提振食慾。'
    rec = dict(REC, fact_zh=fact, nutrition_fact=fact, evidence_quote=fact)
    snap = dict(status='available', records=[rec], by_ingredient={'FOOD000001': 'available'})
    out = ke.explain_snapshot(calls_through, FakeExplainer({'大蒜': '大蒜有助提振食慾。'}), snap)
    assert out['items'] == []
    assert out['dropped'] == [{'ingredient_id': 'FOOD000001', 'reason': 'NOT_GROUNDED_IN_NUTRITION_FACT'}]
    assert out['status'] == 'not_found'


class FakeKnowledgeClient:
    def __init__(self, records):
        self.records, self.calls = records, []

    def search(self, food_ids, vector, model, *, limit_per_ingredient):
        self.calls.append((list(food_ids), vector, model))
        return {'records': [r for r in self.records if r['ingredient_id'] in food_ids], 'index_version': 'idx-1'}


class FakeEmbedder:
    model = 'fake-embedding'

    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts], {'input_tokens': 1, 'output_tokens': 0}


def _no_network(monkeypatch):
    import httpx

    def forbidden(*args, **kwargs):
        pytest.fail('tips_for_menu with fake client/embedder/explainer must not touch the network')
    monkeypatch.setattr(httpx, 'post', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)


def test_tips_for_menu_with_fakes_stays_offline_and_maps_source_label(monkeypatch):
    from app.services import knowledge_explanation as ke
    _no_network(monkeypatch)
    keys = []

    def through(factory, **kw):
        keys.append(kw['call_key'])
        return kw['call']()[0]
    monkeypatch.setattr(ke.spend, 'paid_call', through)
    client = FakeKnowledgeClient([REC])
    out = ke.tips_for_menu(None, final_food_ids=['FOOD000001', 'FOOD000009', 'FOOD000001'], household_id=None,
                           request_tag='tips:r:1', client=client, embedder=FakeEmbedder(),
                           explainer=FakeExplainer({'大蒜': GARLIC_TIP}), names={'FOOD000001': '大蒜'})
    assert client.calls[0][0] == ['FOOD000001', 'FOOD000009']
    assert out['status'] == 'available' and out['knowledge_status'] == 'available'
    assert out['final_food_ids'] == ['FOOD000001', 'FOOD000009'] and out['not_found'] == ['FOOD000009']
    assert out['index_version'] == 'idx-1'
    citation = out['items'][0]['citations'][0]
    assert citation['source_label'] == '測試健康網'
    assert citation['title'] == '大蒜的營養' and citation['published_at'] == '2026-10-01'
    assert citation['source_url'] == 'https://example.test/a' and citation['knowledge_id'] == 'K1'
    assert len(keys) == 2 and all(k.startswith('tips:r:1:') for k in keys)


def test_tips_for_menu_billing_stop_on_retrieval_is_unavailable(monkeypatch):
    from app.services import knowledge_explanation as ke
    _no_network(monkeypatch)

    def broke(factory, **kw):
        raise ke.spend.BudgetExhausted()
    monkeypatch.setattr(ke.spend, 'paid_call', broke)
    out = ke.tips_for_menu(None, final_food_ids=['FOOD000001'], household_id=None, request_tag='tips:r:1',
                           client=FakeKnowledgeClient([REC]), embedder=FakeEmbedder(), explainer=FakeExplainer({}))
    assert out['status'] == 'unavailable' and out['reason'] == 'BUDGET_EXHAUSTED' and out['items'] == []
    assert out['knowledge_status'] == 'unavailable'


def test_tips_for_menu_without_knowledge_service_is_unavailable(monkeypatch):
    from app.services import knowledge_explanation as ke
    _no_network(monkeypatch)
    monkeypatch.setattr('app.services.knowledge.default_knowledge_client', lambda: None)
    out = ke.tips_for_menu(None, final_food_ids=['FOOD000001'], household_id=None, request_tag='tips:r:1',
                           embedder=FakeEmbedder(), explainer=FakeExplainer({}))
    assert out['status'] == 'unavailable' and out['reason'] == 'NOT_CONFIGURED' and out['items'] == []


def test_route_blocked_preview_never_calls_paid_services():
    """營養未通過（例如主食防線）時，路由直接回 unavailable，不檢索、不生成（不需資料庫）。"""
    import uuid
    from app.api import meal_requests as api

    class Row:
        id, household_id, version, payload = uuid.uuid4(), uuid.uuid4(), 3, {}
    menu = {"status": "calculation_incomplete", "final_food_ids": [], "nutrition_passed": False}
    with patch.object(api, "get_owned_request", return_value=Row), \
            patch.object(api.service, "load", return_value=object()), \
            patch.object(api, "_reject_bad_references"), patch.object(api, "_members", return_value={}), \
            patch.object(api.service, "completeness", return_value=[]), \
            patch.object(api.t12_planning, "preview", return_value=menu), \
            patch.object(api.knowledge_explanation, "tips_for_menu") as run, \
            patch.object(api.ingredients, "names") as names:
        out = api.knowledge_tips(Row.id, api.NutritionPreviewBody(recipe_ids=["R001"]),
                                 household_id=Row.household_id, db=None)
    run.assert_not_called()
    names.assert_not_called()
    assert out["status"] == "unavailable" and out["reason"] == "NUTRITION_NOT_PASSED"
    assert out["nutrition_status"] == "calculation_incomplete" and out["input_version"] == 3
    assert out["items"] == [] and out["recipe_ids"] == ["R001"]
