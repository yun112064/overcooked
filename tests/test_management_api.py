import json
import uuid
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.management import router
from app.api.deps import current_auth
from app.api.errors import register_error_handlers
from app.core.config import settings
from app.db.session import get_db
from sqlalchemy.orm import Session
from test_management_foundation import env,ACTORS

@pytest.fixture
def client(env,monkeypatch):
    monkeypatch.setattr(settings,'management_enabled',True)
    app=FastAPI();register_error_handlers(app);app.include_router(router)
    def db():
        with Session(env.engine) as session:
            try:yield session
            finally:session.rollback()
    app.dependency_overrides[get_db]=db
    app.dependency_overrides[current_auth]=lambda:SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))
    with TestClient(app,base_url='https://testserver',headers={'Origin':'http://localhost:8080'}) as c:
        yield c,app

def test_disabled_by_default_and_requires_actual_grants(client,monkeypatch):
    c,app=client
    monkeypatch.setattr(settings,'management_enabled',False)
    assert c.get('/admin/v1/capabilities').json()['error']['code']=='MANAGEMENT_DISABLED'
    monkeypatch.setattr(settings,'management_enabled',True)
    app.dependency_overrides[current_auth]=lambda:SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    assert c.get('/admin/v1/capabilities').status_code==403

def test_http_artifact_and_proposal_use_core_contract(client,env):
    c,_=client
    response=c.post('/admin/v1/artifacts',headers={'Idempotency-Key':'upload-api'},data={'domain':'source_policy','source_kind':'manual_entry','reason':'synthetic evidence'},files={'file':('evidence.txt',b'fixture','text/plain')})
    assert response.status_code==201,response.text
    replay=c.post('/admin/v1/artifacts',headers={'Idempotency-Key':'upload-api'},data={'domain':'source_policy','source_kind':'manual_entry','reason':'synthetic evidence'},files={'file':('evidence.txt',b'fixture','text/plain')})
    assert replay.status_code==201 and replay.json()==response.json()
    assert replay.headers['Idempotency-Replayed']=='true'
    p=env.create()
    assert c.get('/admin/v1/proposals/'+p['proposal_id']).status_code==200
    request=env.expected(p)
    response=c.post('/admin/v1/proposals/'+p['proposal_id']+'/validations',json=request,headers={'Idempotency-Key':'validate-api'})
    assert response.status_code==202
    tid=response.json()['task_id']
    assert c.get('/admin/v1/tasks/'+tid).json()['status']=='queued'
    assert c.get('/admin/v1/tasks/'+tid+'/result').status_code==409
    env.call(ACTORS[1],'run_validation',tid)
    assert c.get('/admin/v1/tasks/'+tid+'/result').json()['formal_generation_ready'] is False

def test_csrf_duplicate_keys_and_client_approval_rejected(client,env):
    c,_=client;p=env.create();path='/admin/v1/proposals/'+p['proposal_id']+'/submit'
    assert c.post(path,json=env.expected(p),headers={'Origin':'https://example.invalid','Idempotency-Key':'forbidden-origin'}).status_code==403
    assert c.post(path,content='{"reason":"a","reason":"b"}',headers={'Content-Type':'application/json','Idempotency-Key':'duplicate'}).status_code==422
    assert c.post(path,json={**env.expected(p),'approved':True},headers={'Idempotency-Key':'authority'}).status_code==422
    assert c.post(path,json=env.expected(p)).status_code==422

def test_non_engineering_edit_keeps_known_and_unknown_source_values(client,env):
    c,_=client;p=env.create()
    body={**env.expected(p),'domain':p['domain'],'content':p['content'],'dependency_pins':p['dependency_pins']}
    body['content']['timeout_seconds']=20
    response=c.post('/admin/v1/proposals/'+p['proposal_id']+'/revisions',json=body,headers={'Idempotency-Key':'edit-api'})
    assert response.status_code==201,response.text
    assert response.json()['revision']==2
    assert response.json()['dependency_pins']['legacy_recipe_source_sha256'] is None

def test_artifact_mime_cannot_disguise_executable_or_invalid_workbook(client):
    c,_=client
    response=c.post('/admin/v1/artifacts',headers={'Idempotency-Key':'bad-xlsx'},data={'domain':'source_policy','source_kind':'workbook','reason':'bad fixture'},files={'file':('x.xlsx',b'not a zip file','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert response.status_code==422


def test_browser_can_validate_immediately_and_resume_its_own_queued_task(client, env):
    c, app = client
    p = env.create()
    path = '/admin/v1/proposals/' + p['proposal_id'] + '/validations'
    headers = {'Idempotency-Key': 'browser-validation'}
    first = c.post(path, params={'run_now': True}, json=env.expected(p), headers=headers)
    assert first.status_code == 202, first.text
    task_path = '/admin/v1/tasks/' + first.json()['task_id']
    result = c.get(task_path + '/result').json()
    assert result['catalog_ready'] is True
    replay = c.post(path, params={'run_now': True}, json=env.expected(p), headers=headers)
    assert replay.json() == first.json()
    assert c.get(task_path + '/result').json() == result
    assert c.get(task_path).json()['attempt'] == 1

    queued = c.post(path, json=env.expected(p), headers={'Idempotency-Key': 'legacy-queued'}).json()
    run_path = '/admin/v1/tasks/' + queued['task_id'] + '/run'
    env.grants(ACTORS[3], ['source_reviewer'], ['source_policy'])
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[3]))
    assert c.post(run_path).status_code == 403
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))
    assert c.post(run_path, headers={'Origin': 'https://example.invalid'}).status_code == 403
    assert c.post(run_path).json()['catalog_ready'] is True


def test_role_admin_can_list_grant_and_revoke_active_roles(client, env):
    c, app = client
    path = '/admin/v1/role-grants'
    query = {'user_id': ACTORS[3]}
    assert c.get(path, params=query).status_code == 403
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[0]))
    before = c.get(path, params=query).json()
    assert before['items'] == [] and 'auditor' in before['roles']
    body = dict(user_id=ACTORS[3], role='auditor', domain_scope=['recipe'], expires_at=None,
                expected_access_revision=before['access_revision'], reason='管理測試')
    created = c.post(path, json=body, headers={'Idempotency-Key': 'browser-grant'})
    assert created.status_code == 201, created.text
    after = c.get(path, params=query).json()
    assert after['items'] == [dict(grant_id=created.json()['resource_id'], role='auditor',
                                   domain_scope=['recipe'], expires_at=None)]
    revoke = c.post(path + '/' + created.json()['resource_id'] + '/revoke',
                    json={'expected_access_revision': after['access_revision'], 'reason': '結束測試'},
                    headers={'Idempotency-Key': 'browser-revoke'})
    assert revoke.status_code == 200, revoke.text
    assert c.get(path, params=query).json()['items'] == []
