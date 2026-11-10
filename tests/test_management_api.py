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
