"""Recipe intake against synthetic SQLite and the marker-verified T01 test DB."""

import hashlib
import json
import os
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.api.deps import current_auth
from app.api.management_recipes import router
from app.db.models import User
from app.management.contracts import VERSION, rules_pin
from app.management.models import tables as T
from app.management.recipe_candidates import get_fixture, import_fixture
from app.management.service import Management, legacy_capture, now
from app.models.recipe import (
    Allergen, IngredientAlias, Recipe, RecipeCover, RecipeDataRelease, RecipeIngredient, RecipeStep, SourceArchive,
)
from test_management_api import client
from test_management_foundation import ACTORS, code, env


def candidate(name='清蒸鮮魚 Ａ', basis='portions', count=' 0950.000 '):
    return {
        'format_version': 'recipe-fixture-v1',
        'source': {'kind': 'ai_fixture', 'reference': 'https://example.invalid/recipe-fixture',
                   'observed_at': '2026-11-07T12:30:00+08:00',
                   'ai_metadata': {'model': 'synthetic-model', 'prompt_version': 'fixture-p1', 'rule_version': 'fixture-r1'}},
        'recipe': {'name': name, 'aliases': [' 清蒸鮮魚 ', '清蒸鮮魚'], 'category': '主菜',
                   'cuisine': '台式', 'meal_type': '午餐',
                   'servings': {'basis': basis, 'raw_text': f'原始文字 {count}', 'count': count},
                   'ingredients': [{'canonical_name': '魚肉', 'raw_text': '  魚肉 120 克  ',
                                    'food_id': 'SYNTHETIC_FOOD', 'amount': ' 00120.2500 ', 'unit': '克',
                                    'weight_g': ' 00120.2500 '}],
                   'steps': ['  將魚肉蒸熟。  ', '盛盤。']},
    }


def dependency_pins(release=None, capture=None):
    return dict(legacy_recipe_release_id=release, legacy_recipe_source_sha256='a' * 64 if release else None,
                legacy_db_capture_sha256=capture, base_publication_id=None, base_publication_manifest_sha256=None,
                ingredient_publication_id=None, ingredient_manifest_sha256=None, source_policy_publication_id=None,
                source_policy_manifest_sha256=None, contract_version=VERSION, **rules_pin())


def request(fixture, pins=None):
    return {'fixture_json': json.dumps(fixture, ensure_ascii=False, indent=2) + '\n',
            'reason': 'Synthetic recipe fixture', 'dependency_pins': pins or dependency_pins()}


def intake(env, key, body, actor=ACTORS[1]):
    with Session(env.engine) as session, session.begin():
        return import_fixture(Management(session, actor), key, body)


def seed_release(session, suffix='sqlite'):
    release_id = f'synthetic-recipe-{suffix}'
    raw_id = f'raw-{suffix}'
    recipe_id = f'recipe-{suffix}'
    session.add(RecipeDataRelease(release_id=release_id, dataset_version=release_id, source_sha256='a' * 64,
                                  schema_version='synthetic-v1', source_kind='xlsx_import'))
    session.flush()
    session.add(IngredientAlias(food_id='SYNTHETIC_FOOD', recipe_release_id=release_id, canonical_name='魚肉'))
    session.add(SourceArchive(raw_id=raw_id, recipe_release_id=release_id, source_name='fixture',
                              source_url='https://example.invalid/legacy-fixture', source_recipe_id=raw_id,
                              recipe_name_raw='清蒸鮮魚 A', servings_raw='950人份', ingredients_raw='魚肉',
                              steps_raw='蒸熟', tags_raw='', content_hash='b' * 64, import_batch_id=release_id))
    session.flush()
    session.add(Recipe(recipe_id=recipe_id, recipe_release_id=release_id, raw_id=raw_id, standard_dish_id='dish-fixture',
                       recipe_name='清蒸鮮魚 A', normalized_name='清蒸鮮魚 A', servings_raw='950人份',
                       original_servings=950, servings_status='declared', category='主菜', meal_type='午餐', cuisine='台式',
                       source_name='fixture', source_url='https://example.invalid/legacy-fixture'))
    session.flush()
    return recipe_id, dependency_pins(release_id, legacy_capture(session))


@pytest.fixture
def recipe_release(env):
    models = (RecipeDataRelease, SourceArchive, IngredientAlias, Allergen, Recipe, RecipeStep, RecipeIngredient, RecipeCover)
    Recipe.metadata.create_all(env.engine, tables=[model.__table__ for model in models])
    with Session(env.engine) as session, session.begin():
        return seed_release(session)


def test_api_preserves_fixture_bytes_and_idempotency(client, env, monkeypatch):
    http, app = client
    app.include_router(router)
    env.grants(ACTORS[1], ['recipe_editor'], ['recipe'])
    path = '/admin/v1/recipe-candidates/fixtures'
    headers = {'Idempotency-Key': 'candidate-intake'}
    body = request(candidate())
    response = http.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    receipt = response.json()
    proposal = receipt['proposal']
    assert proposal['status'] == 'draft' and proposal['content']['logical_recipe_id'].startswith('MR4_')
    assert proposal['content']['standard_dish_id'] is None
    assert proposal['content']['name'] == '清蒸鮮魚 Ａ'
    assert proposal['content']['aliases'] == ['清蒸鮮魚']
    assert proposal['content']['servings']['count'] == '950'
    assert proposal['content']['servings']['basis'] == 'portions'
    line = proposal['content']['ingredients'][0]
    assert line['weight_g']['value'] == '120.25' and line['food_id'] == 'SYNTHETIC_FOOD'
    assert line['weight_basis'] == 'whole_source_recipe_before_cooking'
    assert proposal['content']['declared_allergen_ids'] is None
    assert receipt['source']['ai_metadata']['model'] == 'synthetic-model'
    assert receipt['artifact']['sha256'] == hashlib.sha256(body['fixture_json'].encode('utf-8')).hexdigest()
    assert receipt['artifact']['source_kind'] == 'supplied_document'
    pid = proposal['proposal_id']
    evidence_path = f'/admin/v1/recipe-candidates/{pid}/fixture'
    assert http.get(evidence_path).json() == {**receipt, 'fixture_json': body['fixture_json']}
    replay = http.post(path, json=body, headers=headers)
    assert replay.json() == receipt and replay.headers['Idempotency-Replayed'] == 'true'
    assert http.post(path, json={**body, 'fixture_json': body['fixture_json'] + ' '}, headers=headers).status_code == 409
    assert http.post(path, json=body, headers={**headers, 'Origin': 'https://example.invalid'}).status_code == 403
    monkeypatch.setitem(app.dependency_overrides, current_auth, lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3])))
    assert http.get(evidence_path).status_code == 403
    assert http.post(path, json=body, headers=headers).status_code == 403
    with env.engine.connect() as connection:
        for table in ('artifacts', 'proposals'):
            assert connection.scalar(sa.select(sa.func.count()).select_from(T[table])) == 1
        assert set(connection.scalars(sa.select(T['audit_events'].c.action).where(T['audit_events'].c.domain == 'recipe'))) == {
            'recipe_fixture.import', 'artifact.upload', 'proposal.create',
        }


def test_duplicate_suggestions_do_not_merge_or_replace(env, recipe_release):
    env.grants(ACTORS[1], ['recipe_editor'], ['recipe'])
    recipe_id, pins = recipe_release
    body = request(candidate(), pins)
    first = intake(env, 'duplicate-first', body)
    second = intake(env, 'duplicate-second', body)
    distinct = intake(env, 'duplicate-third', request(candidate('清蒸鮮魚 Ａ版'), pins))
    assert second['proposal']['content']['logical_recipe_id'] != first['proposal']['content']['logical_recipe_id']
    assert second['duplicate_suggestions'][0]['reasons'] == ['EXACT_SOURCE_SHA256', 'NORMALIZED_NAME']
    assert second['duplicate_suggestions'][0]['proposal_id'] == first['proposal']['proposal_id']
    assert second['duplicate_suggestions'][1] == {
        'kind': 'legacy_recipe', 'recipe_id': recipe_id, 'release_id': pins['legacy_recipe_release_id'],
        'name': '清蒸鮮魚 A', 'reasons': ['NORMALIZED_NAME'],
    }
    assert distinct['proposal']['content']['name'] == '清蒸鮮魚 Ａ版'
    assert {'DECLARED_ALIAS', 'SIMILAR_NAME'} <= {
        reason for item in distinct['duplicate_suggestions'] for reason in item['reasons']
    }
    with Session(env.engine) as session:
        assert session.get(Recipe, recipe_id).standard_dish_id == 'dish-fixture'
        assert legacy_capture(session) == pins['legacy_db_capture_sha256']
        assert session.scalar(sa.select(sa.func.count()).select_from(T['review_decisions'])) == 0


def test_batch_unknowns_and_input_rejection_do_not_leak_artifacts(env):
    env.grants(ACTORS[1], ['recipe_editor'], ['recipe'])
    fixture = candidate(basis='batch', count='1')
    line = fixture['recipe']['ingredients'][0]
    line.update(weight_g=None, food_id=None, amount=None)
    body = request(fixture)
    result = intake(env, 'batch', body)
    proposal = result['proposal']
    pid = proposal['proposal_id']
    assert proposal['content']['servings']['basis'] == 'batch'
    assert proposal['content']['servings']['count'] == '1'
    assert proposal['content']['servings']['portions_per_batch'] is None
    assert proposal['content']['ingredients'][0]['weight_g']['status'] == 'unknown'
    assert proposal['content']['ingredients'][0]['weight_g']['value'] is None
    validation = env.call(ACTORS[1], 'request_validation', 'batch-validate', pid, env.expected(proposal))
    report = env.call(ACTORS[1], 'run_validation', validation['task_id'])
    assert not report['catalog_ready']
    assert {'SOURCE_WEIGHT_REQUIRED', 'FOOD_IDENTITY_UNVERIFIED'} <= {issue['code'] for issue in report['issues']}
    code('VALIDATION_REQUIRED', lambda: env.call(ACTORS[1], 'submit', 'batch-submit', pid, env.expected(proposal)))
    fixture['recipe']['approved'] = True
    code('INVALID_INPUT', lambda: intake(env, 'batch-invalid', request(fixture)))
    del fixture['recipe']['approved']
    line['weight_g'] = '1.0000001'  # cannot round a source value to fit the contract
    code('INVALID_INPUT', lambda: intake(env, 'batch-precision', request(fixture)))
    line['weight_g'] = 12.5
    code('INVALID_INPUT', lambda: intake(env, 'batch-float', request(fixture)))
    code('INVALID_INPUT', lambda: intake(env, 'batch-duplicate', {**body, 'fixture_json': '{"recipe": {}, "recipe": {}}'}))
    code('INVALID_INPUT', lambda: intake(env, 'batch-size-limit', {**body, 'fixture_json': '食' * 100000}))
    with env.engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(T['artifacts'])) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(T['proposals'])) == 1


def test_independent_review_publication_and_raw_evidence_survive_revision(env, recipe_release):
    env.grants(ACTORS[1], ['recipe_editor', 'recipe_reviewer'], ['recipe'])
    env.grants(ACTORS[2], ['recipe_reviewer'], ['recipe'])
    _, pins = recipe_release
    body = request(candidate(), pins)
    original = intake(env, 'review-create', body)['proposal']
    pid = original['proposal_id']
    edited_content = {**original['content'], 'name': '人工修訂清蒸鮮魚 Ａ'}
    revised = env.call(ACTORS[1], 'revise', 'review-revise', pid, {
        **env.expected(original), 'domain': 'recipe', 'content': edited_content, 'dependency_pins': pins,
    })
    assert revised['content']['logical_recipe_id'] == original['content']['logical_recipe_id']
    with Session(env.engine) as session:
        evidence = get_fixture(Management(session, ACTORS[2]), pid)
        assert evidence['proposal'] == original and evidence['fixture_json'] == body['fixture_json']
    validation = env.submit(revised)
    review = {**env.expected(revised), 'validation_id': validation['validation_id'],
              'dependency_hash': revised['dependency_hash'], 'scope': 'content', 'verdict': 'approve'}
    code('SELF_REVIEW_FORBIDDEN', lambda: env.call(ACTORS[1], 'review', 'review-self', pid, review))
    content_decision = env.call(ACTORS[2], 'review', 'review-content', pid, review)
    assert env.call(ACTORS[2], 'get', pid)['status'] == 'submitted'
    rights_decision = env.call(ACTORS[2], 'review', 'review-source', pid, {**review, 'scope': 'source_rights'})
    publish_body = {'domain': 'recipe', 'base_publication_id': None, 'reason': 'Synthetic recipe publication',
                    'proposal_refs': [{'proposal_id': pid, 'revision': revised['revision'],
                                       'content_hash': revised['content_hash'], 'validation_id': validation['validation_id'],
                                       'content_approval_id': content_decision['resource_id'],
                                       'source_rights_approval_id': rights_decision['resource_id']}]}
    publication = env.call(ACTORS[2], 'publish', 'recipe-publish', publish_body)
    assert publication['formal_generation_ready'] is False and publication['member_count'] == 2
    with Session(env.engine) as session:
        assert legacy_capture(session) == pins['legacy_db_capture_sha256']
        assert session.scalar(sa.select(T['publications'].c.manifest))['nutrition_readiness'] == 'not_inherited'
        assert session.scalar(sa.select(T['active_bindings'].c.publication_id).where(T['active_bindings'].c.channel == 'recipe_catalog')) is None
    publication_pins = {**pins, 'base_publication_id': publication['publication_id'],
                        'base_publication_manifest_sha256': publication['manifest_hash']}
    later = intake(env, 'review-later', {**body, 'dependency_pins': publication_pins})
    assert later['duplicate_suggestions'][0]['kind'] == 'published_recipe'
    assert later['duplicate_suggestions'][0]['reasons'][0] == 'EXACT_SOURCE_SHA256'
    assert later['proposal']['content']['logical_recipe_id'] != revised['content']['logical_recipe_id']
    assert env.call(ACTORS[1], 'get', pid)['content']['name'] == edited_content['name']


def test_postgres_fixture_import_rolls_back_in_verified_t01_database():
    raw_url = os.getenv('TEST_DATABASE_URL')
    if not raw_url:
        pytest.skip('Dedicated T01 stage-0 runner was not selected')
    url = sa.engine.make_url(raw_url)
    if (url.get_backend_name(), url.host, url.port, url.database) != (
        'postgresql', '127.0.0.1', 55541, 'meal_planner_1102_v3_t01_v4_test',
    ):
        pytest.skip('This case only uses the dedicated T01 test DB')
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            assert connection.scalar(sa.text('SELECT current_database()')) == url.database
            assert connection.scalar(sa.text(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
            )) == 'overcooked-stage0:T01_V4:20261107'
            actor = uuid.uuid4()
            with Session(connection) as session:
                session.add(User(id=actor, email='recipe-fixture@example.invalid', password_hash='synthetic-only'))
                session.flush()
                session.execute(T['role_grants'].insert().values(
                    id=uuid.uuid4().hex, user_id=str(actor), role='recipe_editor', domain_scope=['recipe'],
                    access_revision=1, created_by=str(actor), created_at=now(),
                ))
                _, pins = seed_release(session, 'postgres')
                service = Management(session, actor)
                body = request(candidate(), pins)
                receipt = import_fixture(service, 'pg-recipe-fixture', body)
                assert receipt == import_fixture(service, 'pg-recipe-fixture', body)
                pid = receipt['proposal']['proposal_id']
                assert service.replayed and get_fixture(service, pid)['fixture_json'] == body['fixture_json']
                assert receipt['duplicate_suggestions'][0]['kind'] == 'legacy_recipe'
            transaction.rollback()
        with engine.connect() as connection:
            assert connection.scalar(sa.select(T['proposals'].c.id).where(T['proposals'].c.id == pid)) is None
    finally:
        engine.dispose()
