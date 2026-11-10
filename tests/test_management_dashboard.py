"""Dashboard contract and isolation tests using synthetic management records."""
import uuid
from types import SimpleNamespace

import pytest

from app.api.deps import current_auth
from app.api.management_dashboard import router
from app.management.contracts import validate
from test_management_api import client
from test_management_foundation import ACTORS, env

BASE = '/admin/v1'


@pytest.fixture
def dashboard(client):
    c, app = client
    app.include_router(router)
    return c, app


def test_registry_is_policy_only_and_cursor_cannot_change_collection(dashboard, env):
    c, app = dashboard
    response = c.get(BASE + '/data-types', params={'limit': 2})
    assert response.status_code == 200, response.text
    data = validate('DataTypeCollection', response.json())
    assert data['items'][0]['key'].startswith('admin_')
    later = c.get(BASE + '/data-types', params={'limit': 2, 'cursor': data['next_cursor']}).json()
    assert later['items'][0]['key'] > data['items'][-1]['key']
    assert c.get(BASE + '/tasks', params={'cursor': data['next_cursor']}).json()['error']['code'] == 'INVALID_CURSOR'
    entry = c.get(BASE + '/data-types/admin_review_events').json()
    assert entry['write_policy'] == 'no_direct_write'
    assert c.get(BASE + '/data-types/not-a-registered-table').status_code == 404
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[0]))
    assert c.get(BASE + '/data-types').status_code == 403


def test_scoped_proposal_history_and_diff(dashboard, env):
    c, app = dashboard
    p = env.create()
    env.create()
    path = BASE + '/proposals'
    first = c.get(path, params={'domain': 'source_policy', 'limit': 1}).json()
    validate('ProposalCollection', first)
    second = c.get(path, params={'domain': 'source_policy', 'limit': 1, 'cursor': first['next_cursor']}).json()
    assert second['items'][0]['proposal_id'] != first['items'][0]['proposal_id']
    assert c.get(path, params={'domain': 'recipe', 'cursor': first['next_cursor']}).status_code == 422
    body = {**env.expected(p), 'domain': p['domain'], 'content': {**p['content'], 'timeout_seconds': 21},
            'dependency_pins': p['dependency_pins']}
    base = path + '/' + p['proposal_id']
    assert c.post(base + '/revisions', json=body, headers={'Idempotency-Key': 'dashboard-revise'}).status_code == 201
    old = c.get(base + '/revisions/1').json()
    assert old['revision'] == 1 and old['content']['timeout_seconds'] == 15
    diff = c.get(base + '/diff', params={'base_revision': 1, 'target_revision': 2}).json()
    validate('ProposalDiff', diff)
    assert diff['changes'] == [{'op': 'replace', 'json_pointer': '/content/timeout_seconds', 'before': 15, 'after': 21}]
    env.grants(ACTORS[3], ['catalog_viewer'], ['ingredient'])
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    assert c.get(base + '/diff', params={'base_revision': 1, 'target_revision': 2}).status_code == 404
    assert c.get(path, params={'domain': 'source_policy'}).status_code == 403


def test_current_workflow_task_and_publication_are_separate(dashboard, env):
    c, app = dashboard
    p = env.create()
    base = BASE + '/proposals/' + p['proposal_id']
    body = env.approved(p)
    workflow = c.get(base + '/workflow').json()
    assert workflow['validation']['catalog_ready'] is True
    assert workflow['latest_task']['status'] == 'succeeded'
    assert set(workflow['approvals']) == {'content', 'source_rights'}
    assert 'lease_token' not in c.get(BASE + '/tasks').text
    publication = env.call(ACTORS[2], 'publish', env.key(), body)
    data = c.get(BASE + '/publications', params={'domain': p['domain']}).json()
    validate('PublicationCollection', data)
    assert data['items'] == [publication]
    binding = c.get(BASE + '/bindings/source_policy').json()
    validate('Binding', binding)
    assert binding['publication_id'] is None
    entities = c.get(BASE + '/entities/source_policy', params={'view_kind': 'publication', 'view_id': publication['publication_id']}).json()
    validate('EntityViewCollection', entities)
    assert len(entities['items']) == 1 and entities['items'][0]['approval_state'] == 'managed_approved'
    env.grants(ACTORS[3], ['catalog_viewer'], ['ingredient'])
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    assert c.get(BASE + '/tasks').json()['items'] == []
    assert c.get(base + '/workflow').status_code == 404


def test_draft_artifact_round_trip_preserves_unknown_and_role_boundary(dashboard, env):
    c, app = dashboard
    env.grants(ACTORS[3], ['ingredient_editor'], ['ingredient'])
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    pins = c.get(BASE + '/draft-context', params={'domain': 'ingredient'}).json()
    validate('DependencyPins', pins)
    assert c.get(BASE + '/draft-context', params={'domain': 'recipe'}).status_code == 403
    evidence = c.post(BASE + '/artifacts', headers={'Idempotency-Key': 'ingredient-evidence'},
                      data={'domain': 'ingredient', 'source_kind': 'manual_entry', 'reason': 'fixture'},
                      files={'file': ('evidence.txt', b'unknown allergens', 'text/plain')})
    assert evidence.status_code == 201, evidence.text
    artifact = evidence.json()
    draft = {'food_id': None, 'canonical_name': '測試食材', 'aliases': [], 'declared_allergen_ids': None,
             'sources': [{'artifact_id': artifact['artifact_id'], 'artifact_sha256': artifact['sha256'],
                          'origin_kind': artifact['source_kind'], 'source_policy_version_id': None}]}
    response = c.post(BASE + '/proposals', headers={'Idempotency-Key': 'ingredient-draft'},
                      json={'domain': 'ingredient', 'content': draft, 'dependency_pins': pins, 'reason': 'synthetic review'})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result['status'] == 'draft' and result['content']['declared_allergen_ids'] is None
    assert result['content']['sources'] == draft['sources']


def test_missing_probe_and_metrics_stay_unknown(dashboard):
    c, _ = dashboard
    connections = validate('ConnectionCollection', c.get(BASE + '/connections').json())['items']
    assert connections and all(row['status'] == 'not_configured' and row['latency_ms'] is None and row['checked_at'] is None for row in connections)
    assert c.get(BASE + '/connections/unknown-target').status_code == 404
    metrics = validate('MetricsSummary', c.get(BASE + '/metrics/summary').json())
    assert metrics['database_bytes'] is None and metrics['recipe_count'] is None
    assert 'database_bytes' in metrics['missing_metrics']
    assert metrics['pending_proposal_count'] == 0


def test_overview_review_history_and_audit_keep_their_own_scopes(dashboard, env, monkeypatch):
    c, app = dashboard
    monkeypatch.setenv('PRICE_SCHEDULER_ENABLED', 'false')
    p = env.create()
    validation = env.submit(p)
    env.call(ACTORS[2], 'review', env.key(), p['proposal_id'], {
        **env.expected(p), 'reason': '補齊來源', 'validation_id': validation['validation_id'],
        'dependency_hash': p['dependency_hash'], 'scope': 'content', 'verdict': 'request_changes',
    })
    history_path = BASE + '/proposals/' + p['proposal_id'] + '/review-history'
    assert c.get(history_path).json()['items'][0]['reason'] == '補齊來源'
    assert c.get(BASE + '/overview').json()['proposals']['source_policy']['changes_requested'] == 1
    schedule = {row['key']: row for row in c.get(BASE + '/schedule-configuration').json()['items']}
    assert schedule['recover_expired_jobs']['interval_seconds'] == 60
    assert schedule['price_collection']['configured_mode'] == 'disabled'
    assert c.get(BASE + '/database-status').json()['database_bytes'] is None
    assert c.get(BASE + '/schedule-price-runs').json()['status'] == 'not_configured'
    assert c.get(BASE + '/audit-events').status_code == 403

    env.grants(ACTORS[3], ['auditor'], ['source_policy'])
    env.grants(ACTORS[3], ['catalog_viewer'], ['recipe'])
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    assert c.get(BASE + '/capabilities', params={'include_scopes': True}).json()['readable_domains'] == ['recipe', 'source_policy']
    audit = c.get(BASE + '/audit-events', params={'limit': 1}).json()
    assert audit['domains'] == ['source_policy']
    assert audit['items'][0]['reason'] == '補齊來源'
    assert c.get(BASE + '/audit-events', params={'domain': 'recipe'}).status_code == 403
    assert c.get(BASE + '/audit-events', params={'domain': 'source_policy', 'cursor': audit['next_cursor']}).status_code == 422
    assert 'price_collection' not in {row['key'] for row in c.get(BASE + '/schedule-configuration').json()['items']}
    assert c.get(BASE + '/schedule-price-runs').status_code == 403
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[0]))
    assert c.get(history_path).status_code == 404
    assert c.get(BASE + '/overview').status_code == 403


def test_postgres_dashboard_filters_task_json_in_marked_disposable_database():
    import os

    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.db.models import User
    from app.management import dashboard_read, operations_read
    from app.management.models import tables as tables
    from app.management.service import Management, now
    from app.models.recipe import RecipeDataRelease

    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Run in the isolated Dashboard PostgreSQL environment')
    parsed = sa.engine.make_url(url)
    if (parsed.host, parsed.port, parsed.database) != ('127.0.0.1', 55543, 'meal_planner_1102_v3_admin_v2_test'):
        pytest.skip('This test writes fixtures only to the Dashboard test database')
    engine = sa.create_engine(parsed)
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            marker = conn.scalar(sa.text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))
            assert marker == 'overcooked-stage0:Admin_dashboard_V2:20261107'
            with Session(conn, join_transaction_mode='create_savepoint') as db:
                actor = uuid.uuid4()
                db.add(User(id=actor, email=f'{actor}@example.invalid', password_hash='synthetic-only'))
                db.flush()
                for role, domain in [('source_reviewer', 'source_policy'), ('recipe_editor', 'recipe'), ('price_editor', 'price')]:
                    db.execute(tables['role_grants'].insert().values(
                        id=uuid.uuid4().hex, user_id=str(actor), role=role, domain_scope=[domain],
                        expires_at=None, revoked_at=None, access_revision=1, created_by=str(actor), created_at=now(),
                    ))
                s = Management(db, actor)
                database = operations_read.database_status(s)
                assert database['status'] == 'healthy' and database['database_bytes'] > 0
                assert database['client_connections'] >= 1 and database['query_latency_ms'] >= 0
                db.execute(sa.text('''
                    INSERT INTO price_collection_runs (id, run_key, provider, trigger_type, scheduled_for,
                        started_at, status, duration_ms, ingredient_total, success_count, failure_count, fatal_error)
                    VALUES (:id, :key, 'pxmart', :trigger, :at, :at, 'failed', 100, 2, 1, 1, 'internal only')
                '''), [dict(id=uuid.uuid4(), key='fixture-' + trigger, trigger=trigger, at=now())
                       for trigger in ('scheduled', 'manual')])
                runs = operations_read.scheduled_price_runs(s, 20)['items']
                assert [run['run_key'] for run in runs] == ['fixture-scheduled']
                assert runs[0]['has_error'] and runs[0]['failure_count'] == 1 and 'fatal_error' not in runs[0]
                artifact = s.upload('fixture-evidence', 'source_policy', 'manual_entry', 'text/plain', b'fixture', 'fixture')
                content = dict(provider='pxmart', external_execution_enabled=False, execution_mode='fixtures_only',
                               registered_endpoint_key='pxmart-fixture', minimum_interval_seconds=2, timeout_seconds=15,
                               max_retries=1, freshness_seconds=86400, sources=[
                                   dict(artifact_id=artifact['artifact_id'], artifact_sha256=artifact['sha256'],
                                        origin_kind='manual_entry', source_policy_version_id=None)])
                pins = dashboard_read.draft_context(s, 'source_policy', None, None, None, None)
                proposal = s.create('fixture-proposal', dict(domain='source_policy', content=content, dependency_pins=pins, reason='fixture'))
                receipt = s.request_validation('fixture-validation', proposal['proposal_id'], dict(
                    expected_revision=1, expected_content_hash=proposal['content_hash'], reason='fixture'))
                task = dashboard_read.tasks(s, 100, None)
                validate('TaskCollection', task)
                assert receipt['task_id'] in {entry['task_id'] for entry in task['items']}
                workflow = dashboard_read.proposal_workflow(s, s.proposal(proposal['proposal_id']))
                assert workflow['latest_task']['task_id'] == receipt['task_id']
                assert workflow['validation'] is None
                assert dashboard_read.metrics(s)['running_task_count'] == 0
                release_id = 'dashboard-fixture-' + uuid.uuid4().hex
                db.add(RecipeDataRelease(release_id=release_id, dataset_version=release_id,
                                         source_sha256='0' * 64, schema_version='fixture', source_kind='legacy'))
                db.flush()
                evidence = s.upload('recipe-evidence', 'recipe', 'manual_entry', 'text/plain', b'fixture', 'fixture')
                unknown = dict(status='unknown', value=None, evidence_ids=[], unknown_reason='fixture')
                recipe = dict(logical_recipe_id=None, name='fixture', standard_dish_id=None, aliases=[], category='fixture',
                              cuisine='fixture', meal_type='fixture', servings=dict(count=None, basis='batch', raw_text='fixture',
                              portions_per_batch=None, evidence_ids=[]), declared_allergen_ids=None,
                              ingredients=[dict(line_id='line-1', food_id=None, ingredient_version_id=None,
                                                canonical_name='fixture', raw_text='fixture', amount=unknown, unit=None,
                                                weight_g=unknown, weight_basis='whole_source_recipe_before_cooking')],
                              steps=[dict(step_no=1, instruction='fixture')],
                              sources=[dict(artifact_id=evidence['artifact_id'], artifact_sha256=evidence['sha256'],
                                            origin_kind='manual_entry', source_policy_version_id=None)])
                no_pins = dashboard_read.draft_context(s, 'recipe', None, None, None, None)
                proposal = s.create('recipe-proposal', dict(domain='recipe', content=recipe, dependency_pins=no_pins, reason='fixture'))
                new_pins = dashboard_read.draft_context(s, 'recipe', None, None, release_id, proposal['proposal_id'])
                validate('DependencyPins', new_pins)
                assert new_pins['legacy_recipe_release_id'] == release_id
                assert new_pins['legacy_recipe_source_sha256'] == '0' * 64
                assert new_pins['legacy_db_capture_sha256'] != new_pins['legacy_recipe_source_sha256']
                edited = s.revise('recipe-repin', proposal['proposal_id'], dict(
                    domain='recipe', content=proposal['content'], dependency_pins=new_pins, expected_revision=1,
                    expected_content_hash=proposal['content_hash'], reason='synthetic re-pin'))
                assert edited['revision'] == 2 and edited['dependency_pins'] == new_pins
            transaction.rollback()
    finally:
        engine.dispose()
