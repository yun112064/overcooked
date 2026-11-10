"""Thin rc1 management adapter; disabled by default, no automatic workers."""
import json
from fastapi import APIRouter, Depends, Request, Response, UploadFile, File, Form
from sqlalchemy import select, text
from app.api.deps import current_auth, verify_origin
from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import get_db
from app.management.service import Management, fail
from app.management.service import now, stamp
from app.management.models import tables as T
from app.management.schema_v1 import IMMUTABLE

router=APIRouter(prefix='/admin/v1',tags=['management'],dependencies=[Depends(verify_origin)])

def service(response:Response,auth=Depends(current_auth),db=Depends(get_db)):
    if not settings.management_enabled:fail('MANAGEMENT_DISABLED',status=503)
    if db.bind.dialect.name=='postgresql':
        if db.scalar(text("SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=current_user")) or db.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')")):
            fail('MANAGEMENT_WRITER_PRIVILEGES_UNSAFE',status=503)
        for suffix in IMMUTABLE:
            if db.scalar(text("SELECT has_table_privilege(current_user,:table,'UPDATE,DELETE,TRUNCATE')"),{'table':'management_'+suffix}):
                fail('MANAGEMENT_WRITER_PRIVILEGES_UNSAFE',status=503)
    result=Management(db,auth.user_id)
    result.response=response
    return result

async def body(request:Request):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>2097152:fail('INVALID_INPUT',status=413)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:fail('INVALID_INPUT','JSON 欄位重複。',422)
            result[key]=value
        return result
    def invalid_number(_):fail('INVALID_INPUT','數值需使用契約指定的整數或十進位字串。',422)
    try:result=json.loads(raw,object_pairs_hook=pairs,parse_float=invalid_number,parse_constant=invalid_number)
    except (ValueError,UnicodeDecodeError):fail('INVALID_INPUT',status=422)
    if not isinstance(result,dict):fail('INVALID_INPUT',status=422)
    return result

def key(request):return request.headers.get('Idempotency-Key')
def complete(s,result):
    s.db.commit()
    if s.replayed:s.response.headers['Idempotency-Replayed']='true'
    return result

@router.get('/capabilities')
def capabilities(domain:str|None=None,s=Depends(service)):
    if domain is not None and domain not in ('recipe','ingredient','price','source_policy'):fail('INVALID_INPUT',status=422)
    return s.capabilities(domain)

@router.post('/role-grants',status_code=201)
def grant(request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.grant(key(request),b))

@router.post('/role-grants/{gid}/revoke')
def revoke(gid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.revoke(key(request),gid,b))

@router.post('/artifacts',status_code=201)
async def upload(request:Request,file:UploadFile=File(),domain:str=Form(),source_kind:str=Form(),reason:str=Form(),s=Depends(service)):
    payload=await file.read(16777217)
    return complete(s,s.upload(key(request),domain,source_kind,file.content_type,payload,reason))

@router.post('/proposals',status_code=201)
def create(request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.create(key(request),b))

@router.get('/proposals/{pid}')
def get(pid:str,s=Depends(service)):return s.get(pid)

@router.post('/proposals/{pid}/revisions',status_code=201)
def revise(pid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.revise(key(request),pid,b))

@router.post('/proposals/{pid}/validations',status_code=202)
def validation(pid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.request_validation(key(request),pid,b))

@router.post('/proposals/{pid}/submit')
def submit(pid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.submit(key(request),pid,b))

@router.post('/proposals/{pid}/withdraw')
def withdraw(pid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.withdraw(key(request),pid,b))

@router.post('/proposals/{pid}/reviews')
def review(pid:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.review(key(request),pid,b))

@router.post('/publications',status_code=201)
def publish(request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.publish(key(request),b))

@router.get('/publications/{pid}/manifest')
def manifest(pid:str,s=Depends(service)):
    p=s.row('publications',T['publications'].c.id==pid)
    if not p:fail('NOT_FOUND',status=404)
    s.require(p['domain']+'.read',p['domain'])
    return p['manifest']

@router.get('/tasks/{tid}')
def task(tid:str,s=Depends(service)):
    t=s.row('tasks',T['tasks'].c.id==tid)
    if not t:fail('NOT_FOUND',status=404)
    p=s.proposal(t['params']['proposal_id']);s.require(p['domain']+'.read',p['domain'])
    return {'task_id':t['id'],'kind':t['kind'],'status':t['state'],'revision':t['revision'],'attempt':t['attempt'],
            'observed_at':stamp(now()),'execution_mode':'fixtures_only'}

@router.get('/tasks/{tid}/result')
def task_result(tid:str,s=Depends(service)):
    t=s.row('tasks',T['tasks'].c.id==tid)
    if not t:fail('NOT_FOUND',status=404)
    p=s.proposal(t['params']['proposal_id']);s.require(p['domain']+'.read',p['domain'])
    if t['state']!='succeeded':fail('TASK_NOT_READY')
    return t['result']

@router.post('/bindings/{channel}/plans')
def plan(channel:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.activation_plan(key(request),channel,b))

@router.post('/bindings/{channel}/activate')
def activate(channel:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.activate(key(request),channel,b))

@router.post('/bindings/{channel}/rollback')
def rollback(channel:str,request:Request,b=Depends(body),s=Depends(service)):return complete(s,s.activate(key(request),channel,b,rollback=True))
