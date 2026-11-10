"""Verify management intake against isolated results written by T14."""

import copy
import json
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.api.deps import current_auth
from app.api.management_recipes import router
from app.db.models import BackgroundJob, GenerationResult, Household, HouseholdMembership, MealRequest
from app.management.models import tables
from app.services.generation import FoodDictionary, MenuCandidate, _ai_dishes, _jsonable, persist_result
from test_management_api import client
from test_management_foundation import ACTORS, env


AI_DISH = '隔離測試番茄蛋'
PATH = '/admin/v1/recipe-candidates/generated'


def create_result_tables(engine):
    # PostgreSQL JSONB values use JSON encoding on the disposable SQLite engine.
    assert engine.dialect.name == 'sqlite'
    engine.dialect.type_compiler_instance.visit_JSONB = lambda type_, **kw: 'JSON'
    GenerationResult.metadata.create_all(engine, tables=[
        model.__table__ for model in (Household, HouseholdMembership, MealRequest, BackgroundJob, GenerationResult)
    ])


def seed_generated_result(db, owner, *, name=AI_DISH, steps=None):
    household = Household(name='隔離測試家庭')
    db.add(household)
    db.flush()
    db.add(HouseholdMembership(household_id=household.id, user_id=owner, role='owner'))
    request = MealRequest(household_id=household.id, status='result_ready', payload={'test_private': 'private-marker'})
    db.add(request)
    db.flush()
    job = BackgroundJob(household_id=household.id, meal_request_id=request.id, kind='meal_generation',
                        input_version=1, status='succeeded', idempotency_key='isolated-ai-result', request_hash='a' * 64)
    db.add(job)
    db.flush()
    foods = FoodDictionary([('FOOD000001', '番茄', [], []), ('FOOD000002', '雞蛋', [], [])])
    fake_output = MenuCandidate.model_validate({'dishes': [{
        'dish_name': name, 'aliases': [], 'cooking_methods': ['炒'],
        'ingredients': [
            {'food_id': 'FOOD000001', 'name': '番茄', 'grams_per_n': 42.125},
            {'food_id': 'FOOD000002', 'name': '雞蛋', 'grams_per_n': 63.75},
        ],
        'steps': steps if steps is not None else ['將番茄切塊。', '加入雞蛋炒熟。'],
    }]})
    dishes, _, problems, _ = _ai_dishes(fake_output, foods, [])
    assert not problems
    summary, _ = persist_result(db, job, _jsonable({
        'mode': 'ai_generated_menu', 'mode_label': 'AI 生成菜單', 'fallback_reason': 'DATABASE_MENU_NOT_ADOPTABLE',
        'dishes': [{'source': 'database', 'dish_name': '測試非 AI 菜'}, *dishes],
        'validation': {'hard_constraints_passed': True},
        'request': {'private_marker': 'private-marker'},
        'previous_day': {'private_marker': 'private-history'},
        'evidence': {'menu_model': 'synthetic', 'menu_prompt_version': 'test-prompt', 'menu_schema_version': 'test-schema'},
    }))
    return household.id, uuid.UUID(summary['generation_result_id'])


@pytest.fixture
def intake(client, env):
    http, app = client
    app.include_router(router)
    env.grants(ACTORS[1], ['recipe_editor'], ['recipe'])
    env.grants(ACTORS[2], ['recipe_editor'], ['recipe'])
    create_result_tables(env.engine)
    with Session(env.engine) as db, db.begin():
        household, result = seed_generated_result(db, uuid.UUID(ACTORS[1]))
        other, other_result = seed_generated_result(db, uuid.UUID(ACTORS[2]), name='其他家庭測試菜')
        db.add(HouseholdMembership(household_id=household, user_id=uuid.UUID(ACTORS[2]), role='member'))

    def auth(actor, selected):
        app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
            user_id=uuid.UUID(actor), session=SimpleNamespace(household_id=selected),
        )

    auth(ACTORS[1], household)
    return http, auth, household, result, other, other_result


def test_only_authorized_owner_can_choose_their_own_ai_dishes(intake):
    http, auth, household, result, other, other_result = intake
    listing = http.get(PATH + '/choices')
    assert listing.status_code == 200, listing.text
    assert [(row['name'], row['dish_index']) for row in listing.json()['items']] == [(AI_DISH, 1)]
    assert http.post(PATH + '/preview', json={'result_id': str(result), 'dish_index': 0}).status_code == 404
    assert http.post(PATH + '/preview', json={'result_id': str(other_result), 'dish_index': 1}).status_code == 404
    auth(ACTORS[2], household)
    assert http.get(PATH + '/choices').json()['error']['code'] == 'AI_RECIPE_HOUSEHOLD_OWNER_REQUIRED'
    auth(ACTORS[1], other)
    assert http.get(PATH + '/choices').status_code == 403
    auth(ACTORS[1], None)
    assert http.get(PATH + '/choices').json()['error']['code'] == 'HOUSEHOLD_NOT_SELECTED'


def test_preview_save_and_edit_preserve_per_portion_values_and_source_result(intake, env):
    http, auth, household, result_id, _, _ = intake
    with Session(env.engine) as db:
        original = copy.deepcopy(db.get(GenerationResult, result_id).payload)
    selection = {'result_id': str(result_id), 'dish_index': 1}
    response = http.post(PATH + '/preview', json=selection)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert 'private-' not in response.text
    assert [item['grams_per_n'] for item in preview['snapshot']['ingredients']] == ['42.125', '63.75']
    with env.engine.connect() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(tables['proposals'])) == 0

    def save(payload, key):
        return http.post(PATH + '/drafts', json=payload, headers={'Idempotency-Key': key})

    payload = {**selection, 'snapshot_sha256': preview['snapshot_sha256']}
    assert save({**payload, 'snapshot_sha256': '0' * 64}, 'tampered').status_code == 409
    saved = save(payload, 'save-dish')
    assert saved.status_code == 201, saved.text
    draft = saved.json()['proposal']
    pid = draft['proposal_id']
    content = draft['content']
    assert draft['status'] == 'draft' and content['servings']['count'] is None
    assert all(line['weight_g']['value'] is None and line['amount']['value'] is None for line in content['ingredients'])
    assert '42.125 g／n=1' in content['ingredients'][0]['raw_text']
    assert save(payload, 'save-dish').headers['Idempotency-Replayed'] == 'true'
    assert save(payload, 'different-key').json()['proposal']['proposal_id'] == pid
    assert http.post(PATH + '/preview', json=selection).json()['existing_proposal_id'] == pid
    auth(ACTORS[2], household)
    assert save(payload, 'not-owner').status_code == 403
    auth(ACTORS[1], household)

    content['name'] += '（修訂）'
    content['servings']['count'] = '2'
    revision = {'domain':'recipe', 'dependency_pins': draft['dependency_pins'], 'content':content,
                'reason':'synthetic revision', 'expected_revision':1, 'expected_content_hash':draft['content_hash']}
    url = f'/admin/v1/proposals/{pid}/revisions'
    assert http.post(url, json={**revision, 'content':{**content, 'sources':[]}},
                     headers={'Idempotency-Key':'remove-source'}).status_code == 422
    revised = http.post(url, json=revision, headers={'Idempotency-Key':'edit-dish'})
    assert revised.status_code == 201, revised.text
    assert revised.json()['status'] == 'draft' and revised.json()['revision'] == 2
    source = http.get(f'{PATH}/{pid}/source')
    assert source.json() == preview['snapshot']
    with Session(env.engine) as db:
        assert db.get(GenerationResult, result_id).payload == original
        for name, count in (('proposals', 1), ('artifacts', 1), ('proposal_revisions', 2),
                            ('tasks', 0), ('review_decisions', 0), ('publications', 0)):
            assert db.scalar(sa.select(sa.func.count()).select_from(tables[name])) == count
        stored = db.scalar(sa.select(tables['artifacts'].c.payload))
        assert json.loads(stored) == preview['snapshot']
