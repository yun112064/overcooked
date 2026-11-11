"""T11 雲端營養小知識：T12 之後的即時 API 與服務（資料庫測試沿用 T08 的合成家庭；服務測試不連網）。"""
import json
import unittest
from unittest.mock import patch

import pytest

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
        menu = {"status": "nutrition_passed", "final_food_ids": ["FOOD000039", "FOOD001566"]}
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


# --- 服務層（不需資料庫） ---

REC = dict(knowledge_id='K1', source_version='v', ingredient_id='FOOD000001', ingredient_name='大蒜',
           fact_zh='食用大蒜有助提振食慾；一次吃太多可能造成腹瀉。', conditions='限一般食用量。',
           # Synthetic evidence fixture, not an approval of production source content.
           nutrition_fact='食用大蒜有助提振食慾；一次吃太多可能造成腹瀉。',
           evidence_quote='食用大蒜有助提振食慾；一次吃太多可能造成腹瀉。',
           evidence_type='食材直接衛教', source_url='https://example.test/a', status='approved')


class FakeExplainer:
    model = 'gpt-5.4-mini-2026-03-17'

    def __init__(self, tips):
        self.tips = tips

    def respond(self, input_text):
        data = json.loads(input_text)
        tip = self.tips[data['ingredient_name']]
        return json.dumps({'tip_zh': tip, 'citations': [data['knowledge'][0]['knowledge_id']]},
                          ensure_ascii=False), {'input_tokens': 1, 'output_tokens': 1}


def calls_through(**kw):
    return kw['call']()[0]


def test_explain_snapshot_keeps_verified_and_drops_overclaim():
    ke = pytest.importorskip('app.services.knowledge_explanation')
    rec2 = dict(REC, knowledge_id='K2', ingredient_id='FOOD000002', ingredient_name='辣椒',
                fact_zh='規律吃辣椒，可能減輕飯後胰島素升高。',
                nutrition_fact='規律吃辣椒，可能減輕飯後胰島素升高。',
                evidence_quote='規律吃辣椒，可能減輕飯後胰島素升高。')
    snap = dict(status='available', records=[REC, rec2],
                by_ingredient={'FOOD000001': 'available', 'FOOD000002': 'available', 'FOOD000003': 'not_found'})
    out = ke.explain_snapshot(calls_through, FakeExplainer({'大蒜': '大蒜有助提振食慾，但吃太多可能腹瀉。',
                                                            '辣椒': '辣椒保證降血糖。'}), snap)
    assert [i['tip_zh'] for i in out['items']] == ['大蒜有助提振食慾，但吃太多可能腹瀉。']
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
