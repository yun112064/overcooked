"""Transactional management workflow. All public mutations use the command ledger.

Caller commits the session before returning success. No network/model providers are
imported here. New publications never update legacy recipe/nutrition/price tables.
"""
import copy
import hashlib
import re
import uuid
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from app.api.errors import ApiError
from app.db.models import User
from app.services.recipe_step_style import check_recipe_steps
from app.services.recipe_volume import VOLUME_UNITS, volume_in_ml
from .contracts import POLICY, VERSION, canonical, digest, rules_pin, validate
from .models import tables as T

def now(): return datetime.now(timezone.utc)
def ident(): return uuid.uuid4().hex
def utc(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def stamp(value): return utc(value).isoformat().replace('+00:00','Z')
def fail(code, message='管理操作無法完成。', status=409): raise ApiError(status,code,message)


def recipe_step_review_issues(steps):
    issues = []
    for index, step in enumerate(steps):
        number = step['step_no']
        checks = check_recipe_steps([(number, step['instruction'])])
        for finding in checks['findings']:
            if finding['status'] == 'needs_review':
                issues.append(dict(
                    code='RECIPE_STEP_STYLE_REVIEW_REQUIRED', severity='blocking',
                    json_pointer=f'/steps/{index}/instruction',
                    message=f"第 {number} 步需確認。{''.join(finding['reasons'])}",
                ))
    return issues


class Management:
    def __init__(self, session, actor_id):
        self.db=session
        self.actor=str(uuid.UUID(str(actor_id)))
        self.replayed=False

    def row(self,name,where,lock=False):
        q=sa.select(T[name]).where(where)
        if lock:q=q.with_for_update()
        result=self.db.execute(q).mappings().one_or_none()
        return dict(result) if result else None

    def grants(self, actor=None):
        t=T['role_grants']
        return list(self.db.execute(sa.select(t).where(t.c.user_id==(actor or self.actor),t.c.revoked_at.is_(None),
                         sa.or_(t.c.expires_at.is_(None),t.c.expires_at>now()))).mappings())

    def require(self,permission,domain=None,actor=None):
        for grant in self.grants(actor):
            if permission in POLICY['role_presets'].get(grant['role'],[]) and (domain is None or domain in grant['domain_scope']):
                return grant
        fail('FORBIDDEN','沒有此管理操作的權限。',403)

    def access_lock(self,exclusive=False):
        q=sa.select(T['access_state']).where(T['access_state'].c.id=='global').with_for_update(read=not exclusive)
        result=self.db.execute(q).mappings().one_or_none()
        if result is None:fail('MANAGEMENT_SCHEMA_REQUIRED',status=503)
        return result['revision']

    def capabilities(self,domain=None):
        self.require('management.enter')
        grants=[g for g in self.grants() if domain is None or domain in g['domain_scope']]
        return dict(contract_version=VERSION,user_id=self.actor,access_revision=self.db.scalar(sa.select(T['access_state'].c.revision)),
                    roles=sorted({g['role'] for g in grants}),permissions=sorted({p for g in grants for p in POLICY['role_presets'][g['role']]}),external_execution_enabled=False)

    def command(self,operation,scope,key,body,permission,domain,action,exclusive=False,bootstrap=False):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}',key):fail('INVALID_INPUT','需要有效的 Idempotency-Key。',422)
        reason=body.get('reason','')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:fail('INVALID_INPUT','需要有效的操作原因。',422)
        self.replayed=False
        with self.db.begin_nested():
            self.access_lock(exclusive)
            if not bootstrap:self.require(permission,domain)
            t=T['commands']
            condition=sa.and_(t.c.actor_id==self.actor,t.c.operation==operation,t.c.resource_scope==scope,t.c.idempotency_key==key)
            request_hash=digest('request',{'operation':operation,'scope':scope,'body':body,'contract_version':VERSION})
            existing=self.row('commands',condition)
            if existing:
                if existing['request_hash']!=request_hash:fail('IDEMPOTENCY_CONFLICT')
                self.replayed=True
                return existing['receipt']
            cid=ident()
            try:
                with self.db.begin_nested():
                    self.db.execute(t.insert().values(id=cid,actor_id=self.actor,operation=operation,resource_scope=scope,
                        idempotency_key=key,request_hash=request_hash,receipt=None,state='pending',created_at=now()))
            except IntegrityError:
                existing=self.row('commands',condition)
                if existing is None or existing['request_hash']!=request_hash:fail('IDEMPOTENCY_CONFLICT')
                self.replayed=True
                return existing['receipt']
            result,resource,before,after=action(cid)
            self.db.execute(T['audit_events'].insert().values(id=ident(),command_id=cid,actor_id=self.actor,domain=domain or 'access',
                resource_id=resource,action=operation,reason=reason,before_hash=before,after_hash=after,created_at=now()))
            self.db.execute(t.update().where(t.c.id==cid).values(receipt=result,state='completed'))
            return result

    def bootstrap(self,reason):
        """Offline only: seed a grant for an EXISTING user, never a password."""
        def action(cid):
            if self.db.get(User,uuid.UUID(self.actor)) is None:fail('NOT_FOUND',status=404)
            if self.grants():fail('BOOTSTRAP_ALREADY_USED')
            if self.db.scalar(sa.select(sa.func.count()).select_from(T['role_grants'])):fail('BOOTSTRAP_ALREADY_USED')
            grant=ident()
            self.db.execute(T['role_grants'].insert().values(id=grant,user_id=self.actor,role='access_admin',domain_scope=POLICY['domains'],
                expires_at=None,revoked_at=None,access_revision=2,created_by=self.actor,created_at=now()))
            self.db.execute(T['access_state'].update().values(revision=2))
            return {'grant_id':grant,'access_revision':2},grant,None,None
        return self.command('access.bootstrap','global','initial-bootstrap',{'reason':reason},None,None,action,exclusive=True,bootstrap=True)

    def grant(self,key,body):
        body=validate('RoleGrantCreate',body)
        def action(cid):
            target=str(uuid.UUID(body['user_id']))
            if target==self.actor:fail('SELF_GRANT_FORBIDDEN')
            current=self.db.scalar(sa.select(T['access_state'].c.revision))
            if current!=body['expected_access_revision']:fail('VERSION_CONFLICT')
            admin=self.require('access.manage')
            if not set(body['domain_scope'])<=set(admin['domain_scope']):fail('FORBIDDEN',status=403)
            if self.db.get(User,uuid.UUID(target)) is None:fail('NOT_FOUND',status=404)
            expiry=datetime.fromisoformat(body['expires_at'].replace('Z','+00:00')) if body['expires_at'] else None
            if expiry and expiry<=now():fail('INVALID_INPUT',status=422)
            t=T['role_grants']
            if self.row('role_grants',sa.and_(t.c.user_id==target,t.c.role==body['role'],t.c.revoked_at.is_(None))):fail('GRANT_EXISTS')
            gid=ident()
            self.db.execute(t.insert().values(id=gid,user_id=target,role=body['role'],domain_scope=body['domain_scope'],expires_at=expiry,
                revoked_at=None,access_revision=current+1,created_by=self.actor,created_at=now()))
            self.db.execute(T['access_state'].update().values(revision=current+1))
            return {'command_id':cid,'status':'completed','resource_id':gid,'task_id':None},gid,None,None
        return self.command('role_grant.create','grants',key,body,'access.manage',None,action,exclusive=True)

    def revoke(self,key,gid,body):
        body=validate('RoleGrantRevoke',body)
        def action(cid):
            g=self.row('role_grants',T['role_grants'].c.id==gid,True)
            if not g:fail('NOT_FOUND',status=404)
            if g['user_id']==self.actor:fail('SELF_GRANT_FORBIDDEN')
            admin=self.require('access.manage')
            if not set(g['domain_scope'])<=set(admin['domain_scope']):fail('FORBIDDEN',status=403)
            current=self.db.scalar(sa.select(T['access_state'].c.revision))
            if current!=body['expected_access_revision']:fail('VERSION_CONFLICT')
            if g['role']=='access_admin':
                remaining=self.db.scalar(sa.select(sa.func.count()).select_from(T['role_grants']).where(T['role_grants'].c.role=='access_admin',
                    T['role_grants'].c.id!=gid,T['role_grants'].c.revoked_at.is_(None),sa.or_(T['role_grants'].c.expires_at.is_(None),T['role_grants'].c.expires_at>now())))
                if not remaining:fail('LAST_ACCESS_ADMIN')
            self.db.execute(T['role_grants'].update().where(T['role_grants'].c.id==gid).values(revoked_at=now(),access_revision=current+1))
            self.db.execute(T['access_state'].update().values(revision=current+1))
            return {'command_id':cid,'status':'completed','resource_id':gid,'task_id':None},gid,None,None
        return self.command('role_grant.revoke',gid,key,body,'access.manage',None,action,exclusive=True)

    def upload(self,key,domain,kind,media_type,payload,reason):
        if domain not in POLICY['domains'] or kind not in ('supplied_document','workbook','manual_entry'):fail('INVALID_INPUT',status=422)
        if not 0<len(payload)<=16777216:fail('INVALID_INPUT',status=413)
        if media_type not in ('text/plain','application/json','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'):fail('INVALID_INPUT',status=422)
        try:
            if media_type=='text/plain':payload.decode('utf-8')
            if media_type=='application/json':json.loads(payload)
            if media_type.endswith('spreadsheetml.sheet'):
                with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
                    entries=workbook.infolist()
                    if 'xl/workbook.xml' not in workbook.namelist() or len(entries)>1000 or sum(e.file_size for e in entries)>67108864:raise ValueError()
                    if any('..' in e.filename.split('/') or e.filename.startswith('/') or '\\' in e.filename or e.filename.lower().endswith('.bin') or e.file_size>max(e.compress_size,1)*1000 for e in entries):raise ValueError()
        except (ValueError,UnicodeDecodeError,zipfile.BadZipFile):fail('INVALID_ARTIFACT','來源檔內容與允許格式不符。',422)
        sha=hashlib.sha256(payload).hexdigest()
        body={'domain':domain,'kind':kind,'media_type':media_type,'sha256':sha,'reason':reason}
        def action(cid):
            aid=ident();created=now()
            self.db.execute(T['artifacts'].insert().values(id=aid,domain=domain,sha256=sha,media_type=media_type,byte_count=len(payload),
                source_kind=kind,payload=payload,created_by=self.actor,created_at=created))
            result=dict(artifact_id=aid,domain=domain,sha256=sha,media_type=media_type,byte_count=len(payload),source_kind=kind,original_file_verified=False,created_at=stamp(created))
            return result,aid,None,sha
        return self.command('artifact.upload',domain,key,body,'artifact.upload',domain,action)

    def proposal(self,pid,lock=False):
        p=self.row('proposals',T['proposals'].c.id==pid,lock)
        if not p:fail('NOT_FOUND',status=404)
        return p

    def revision(self,p):
        return self.row('proposal_revisions',sa.and_(T['proposal_revisions'].c.proposal_id==p['id'],T['proposal_revisions'].c.revision==p['current_revision']))

    def view(self,p):
        r=self.revision(p)
        return dict(proposal_id=p['id'],domain=p['domain'],revision=p['current_revision'],status=p['state'],content=r['content'],dependency_pins=r['dependency_pins'],
                    content_hash=r['content_hash'],dependency_hash=r['dependency_hash'],created_by=p['created_by'],contributors=p['contributor_ids'],
                    created_at=stamp(p['created_at']),updated_at=stamp(p['updated_at']))

    def get(self,pid):
        p=self.proposal(pid)
        try:self.require(p['domain']+'.read',p['domain'])
        except ApiError:fail('NOT_FOUND',status=404)
        return self.view(p)

    def write_revision(self,pid,number,domain,content,pins):
        self.db.execute(T['proposal_revisions'].insert().values(proposal_id=pid,revision=number,content=content,dependency_pins=pins,
            content_hash=digest('content',{'domain':domain,'content':content}),dependency_hash=digest('dependencies',pins),created_by=self.actor,created_at=now()))

    def create(self,key,body):
        body=validate('ProposalCreate',body);domain=body['domain']
        def action(cid):
            content=copy.deepcopy(body['content']);logical=content.get('logical_recipe_id') if domain=='recipe' else content.get('food_id') if domain=='ingredient' else None
            if logical is None:
                logical=('MR4_' if domain=='recipe' else 'MF4_' if domain=='ingredient' else 'MX4_')+ident()[:28]
                if domain=='recipe':content['logical_recipe_id']=logical
                if domain=='ingredient':content['food_id']=logical
            elif not self.logical_exists(domain,logical,body['dependency_pins']):fail('ENTITY_NOT_FOUND')
            pid=ident();created=now()
            self.db.execute(T['proposals'].insert().values(id=pid,domain=domain,logical_id=logical,current_revision=1,state='draft',
                contributor_ids=[self.actor],created_by=self.actor,created_at=created,updated_at=created))
            self.write_revision(pid,1,domain,content,body['dependency_pins'])
            result=self.view(self.proposal(pid))
            return result,pid,None,result['content_hash']
        return self.command('proposal.create',domain,key,body,domain+'.edit',domain,action)

    def logical_exists(self,domain,logical,pins):
        if pins['base_publication_id']:
            return self.row('publication_members',sa.and_(T['publication_members'].c.publication_id==pins['base_publication_id'],T['publication_members'].c.logical_id==logical)) is not None
        if pins['legacy_recipe_release_id'] and domain in ('recipe','ingredient'):
            from app.models.recipe import Recipe,IngredientAlias
            model=Recipe if domain=='recipe' else IngredientAlias
            key=model.recipe_id if domain=='recipe' else model.food_id
            return self.db.scalar(sa.select(key).where(key==logical,model.recipe_release_id==pins['legacy_recipe_release_id'])) is not None
        return False

    def expected(self,p,body):
        r=self.revision(p)
        if p['current_revision']!=body['expected_revision'] or r['content_hash']!=body['expected_content_hash']:fail('VERSION_CONFLICT')
        return r

    def revise(self,key,pid,body):
        body=validate('ProposalRevisionCreate',body);domain=self.proposal(pid)['domain']
        def action(cid):
            p=self.proposal(pid,True);r=self.expected(p,body)
            if p['state'] not in ('draft','changes_requested','approved','rejected'):fail('INVALID_STATE')
            if body['domain']!=domain:fail('INVALID_INPUT',status=422)
            field='logical_recipe_id' if domain=='recipe' else 'food_id' if domain=='ingredient' else None
            if field and body['content'][field]!=p['logical_id']:fail('ENTITY_ID_IMMUTABLE')
            number=p['current_revision']+1
            self.write_revision(pid,number,domain,body['content'],body['dependency_pins'])
            self.db.execute(T['proposals'].update().where(T['proposals'].c.id==pid).values(current_revision=number,state='draft',
                contributor_ids=sorted(set(p['contributor_ids'])|{self.actor}),updated_at=now()))
            result=self.view(self.proposal(pid))
            return result,pid,r['content_hash'],result['content_hash']
        return self.command('proposal.revise',pid,key,body,domain+'.edit',domain,action)

    def dependency_issues(self,pins,domain):
        issues=[]
        if pins['contract_version']!=VERSION or any(pins[k]!=v for k,v in rules_pin().items()):issues.append('RULES_CHANGED')
        for field,hashfield,expected_domain in [('base_publication_id','base_publication_manifest_sha256',domain),
              ('ingredient_publication_id','ingredient_manifest_sha256','ingredient'),('source_policy_publication_id','source_policy_manifest_sha256','source_policy')]:
            if pins[field]:
                row=self.row('publications',T['publications'].c.id==pins[field])
                if not row or row['domain']!=expected_domain or row['manifest_hash']!=pins[hashfield]:issues.append('DEPENDENCY_CHANGED')
        if pins['legacy_recipe_release_id']:
            from app.models.recipe import RecipeDataRelease
            row=self.db.get(RecipeDataRelease,pins['legacy_recipe_release_id'])
            if not row or row.source_sha256!=pins['legacy_recipe_source_sha256'] or legacy_capture(self.db)!=pins['legacy_db_capture_sha256']:issues.append('LEGACY_CAPTURE_CHANGED')
        return issues

    def issues(self,p,r):
        codes=self.dependency_issues(r['dependency_pins'],p['domain']);content=r['content']
        for source in content['sources']:
            artifact=self.row('artifacts',T['artifacts'].c.id==source['artifact_id'])
            if not artifact or artifact['domain']!=p['domain'] or artifact['sha256']!=source['artifact_sha256'] or artifact['source_kind']!=source['origin_kind']:codes.append('SOURCE_EVIDENCE_REQUIRED')
            if source['source_policy_version_id']:
                member=self.row('publication_members',sa.and_(T['publication_members'].c.publication_id==r['dependency_pins']['source_policy_publication_id'],T['publication_members'].c.version_id==source['source_policy_version_id']))
                if not member:codes.append('SOURCE_POLICY_UNVERIFIED')
        if p['domain']=='recipe':
            lines=content['ingredients']
            if len({x['line_id'] for x in lines})!=len(lines):codes.append('DUPLICATE_LINE_ID')
            if [x['step_no'] for x in content['steps']]!=list(range(1,len(content['steps'])+1)):codes.append('STEP_ORDER_INVALID')
            if content['servings']['count'] is None:codes.append('SERVINGS_REQUIRED')
            if any(x['weight_g']['value'] is None or Decimal(x['weight_g']['value'])<=0 for x in lines):codes.append('SOURCE_WEIGHT_REQUIRED')
            source_ids={x['artifact_id'] for x in content['sources']}
            if any(not x['weight_g']['evidence_ids'] or not set(x['weight_g']['evidence_ids'])<=source_ids for x in lines):codes.append('SOURCE_WEIGHT_EVIDENCE_REQUIRED')
            for line in lines:
                if not self.food_matches(line,r['dependency_pins']):codes.append('FOOD_IDENTITY_UNVERIFIED')
                if str(line['unit']).strip().casefold() in VOLUME_UNITS:
                    amount = line['amount']
                    if amount['status'] != 'declared' or volume_in_ml(amount['value'], line['unit']) is None:
                        codes.append('SOURCE_VOLUME_REQUIRED')
                    if not amount['evidence_ids'] or not set(amount['evidence_ids']) <= source_ids:
                        codes.append('SOURCE_VOLUME_EVIDENCE_REQUIRED')
        if p['domain']=='ingredient' and content['food_id'].startswith('MF4_'):codes.append('NEW_FOOD_ID_NOT_ENABLED')
        if p['domain']=='price':codes.append('PRICE_DOMAIN_RULES_REQUIRED')
        targets={'pxmart':'pxmart-fixture','uniprosperity':'uniprosperity-fixture','supplied_document':'manual-fixture','ai_fixture':'ai-fixture'}
        if p['domain']=='source_policy' and content['registered_endpoint_key']!=targets.get(content['provider']):codes.append('TARGET_NOT_REGISTERED')
        issues = [dict(code=c,severity='blocking',json_pointer='',message=c) for c in sorted(set(codes))]
        if p['domain'] == 'recipe':
            issues.extend(recipe_step_review_issues(content['steps']))
        return issues

    def food_matches(self,line,pins):
        if not line['food_id']:return False
        if pins['ingredient_publication_id']:
            member=self.row('publication_members',sa.and_(T['publication_members'].c.publication_id==pins['ingredient_publication_id'],T['publication_members'].c.logical_id==line['food_id']))
            if member and member['version_id']==line['ingredient_version_id']:
                entity=self.row('entity_versions',T['entity_versions'].c.id==member['version_id'])
                return bool(entity and entity['content']['canonical_name']==line['canonical_name'])
        if pins['legacy_recipe_release_id'] and line['ingredient_version_id'] is None:
            from app.models.recipe import IngredientAlias
            return self.db.scalar(sa.select(IngredientAlias.food_id).where(IngredientAlias.food_id==line['food_id'],
                IngredientAlias.recipe_release_id==pins['legacy_recipe_release_id'],IngredientAlias.canonical_name==line['canonical_name'])) is not None
        return False

    def request_validation(self,key,pid,body):
        body=validate('RevisionCommand',body);domain=self.proposal(pid)['domain']
        def action(cid):
            p=self.proposal(pid,True);r=self.expected(p,body)
            if p['state'] not in ('draft','changes_requested'):fail('INVALID_STATE')
            tid=ident()
            self.db.execute(T['tasks'].insert().values(id=tid,command_id=cid,kind='proposal_validation',
                params={'proposal_id':pid,'revision':p['current_revision'],'content_hash':r['content_hash']},state='queued',revision=1,attempt=0,fencing=0,
                lease_token=None,lease_until=None,result=None,created_by=self.actor,created_at=now()))
            return dict(command_id=cid,status='accepted',resource_id=pid,task_id=tid),pid,None,None
        return self.command('proposal.validate',pid,key,body,domain+'.submit',domain,action)

    def run_validation(self,tid):
        """Explicit local executor for pure validations; no daemon is started."""
        with self.db.begin_nested():
            self.access_lock()
            task=self.row('tasks',T['tasks'].c.id==tid,True)
            if not task:fail('NOT_FOUND',status=404)
            p=self.proposal(task['params']['proposal_id'],True)
            self.require(p['domain']+'.submit',p['domain'],actor=task['created_by'])
            if task['created_by']!=self.actor:fail('FORBIDDEN',status=403)
            if task['state']=='succeeded':return task['result']
            if task['state']!='queued':fail('INVALID_STATE')
            if p['current_revision']!=task['params']['revision']:fail('VERSION_CONFLICT')
            r=self.revision(p);issues=self.issues(p,r);vid=ident();created=now()
            self.db.execute(T['validation_runs'].insert().values(id=vid,proposal_id=p['id'],revision=p['current_revision'],content_hash=r['content_hash'],
                dependency_hash=r['dependency_hash'],rule_set_version=rules_pin()['rule_set_version'],issues=issues,catalog_ready=not issues,created_by=self.actor,created_at=created))
            result=dict(validation_id=vid,proposal_id=p['id'],revision=p['current_revision'],content_hash=r['content_hash'],dependency_hash=r['dependency_hash'],
                rule_set_version=rules_pin()['rule_set_version'],evaluated_at=stamp(created),catalog_ready=not issues,formal_generation_ready=False,issues=issues)
            self.db.execute(T['task_attempts'].insert().values(task_id=tid,attempt=task['attempt']+1,fencing=task['fencing']+1,started_at=created,finished_at=created,outcome='succeeded'))
            self.db.execute(T['tasks'].update().where(T['tasks'].c.id==tid).values(state='succeeded',revision=task['revision']+1,attempt=task['attempt']+1,fencing=task['fencing']+1,result=result))
            self.db.execute(T['audit_events'].insert().values(id=ident(),command_id=task['command_id'],actor_id=self.actor,domain=p['domain'],resource_id=vid,
                action='validation.completed',reason='Explicit pure validation executor',before_hash=None,after_hash=r['content_hash'],created_at=created))
            return result

    def validation(self,p,r,vid=None):
        t=T['validation_runs']
        row=self.db.execute(sa.select(t).where(t.c.proposal_id==p['id'],t.c.revision==p['current_revision']).order_by(t.c.created_at.desc(),t.c.id.desc()).limit(1)).mappings().first()
        if not row or (vid and row['id']!=vid) or not row['catalog_ready'] or row['content_hash']!=r['content_hash'] or row['dependency_hash']!=r['dependency_hash'] or self.issues(p,r):fail('VALIDATION_REQUIRED')
        return row

    def submit(self,key,pid,body):
        body=validate('RevisionCommand',body);domain=self.proposal(pid)['domain']
        def action(cid):
            p=self.proposal(pid,True);r=self.expected(p,body)
            if p['state'] not in ('draft','changes_requested'):fail('INVALID_STATE')
            if domain == 'recipe':
                issues = recipe_step_review_issues(r['content']['steps'])
                if issues:
                    raise ApiError(409, 'VALIDATION_REQUIRED', '請先處理需要人工確認的食譜步驟。', {'issues': issues})
            self.validation(p,r)
            self.db.execute(T['proposals'].update().where(T['proposals'].c.id==pid).values(state='submitted',updated_at=now()))
            return dict(command_id=cid,status='completed',resource_id=pid,task_id=None),pid,r['content_hash'],r['content_hash']
        return self.command('proposal.submit',pid,key,body,domain+'.submit',domain,action)

    def withdraw(self,key,pid,body):
        body=validate('RevisionCommand',body);domain=self.proposal(pid)['domain']
        def action(cid):
            p=self.proposal(pid,True);r=self.expected(p,body)
            if p['state'] not in ('draft','changes_requested','submitted','approved'):fail('INVALID_STATE')
            self.db.execute(T['proposals'].update().where(T['proposals'].c.id==pid).values(state='withdrawn',updated_at=now()))
            return dict(command_id=cid,status='completed',resource_id=pid,task_id=None),pid,r['content_hash'],r['content_hash']
        return self.command('proposal.withdraw',pid,key,body,domain+'.edit',domain,action)

    def review(self,key,pid,body):
        body=validate('ReviewCommand',body);domain=self.proposal(pid)['domain']
        permission=domain+'.review' if body['scope']=='content' else 'source.rights_review'
        def action(cid):
            p=self.proposal(pid,True);r=self.expected(p,body)
            if p['state']!='submitted':fail('INVALID_STATE')
            if self.actor in p['contributor_ids']:fail('SELF_REVIEW_FORBIDDEN')
            if body['dependency_hash']!=r['dependency_hash']:fail('DEPENDENCY_CHANGED')
            self.validation(p,r,body['validation_id'])
            grant=self.require(permission,domain);rid=ident()
            self.db.execute(T['review_decisions'].insert().values(id=rid,proposal_id=pid,revision=p['current_revision'],validation_id=body['validation_id'],
                scope=body['scope'],verdict=body['verdict'],content_hash=r['content_hash'],dependency_hash=r['dependency_hash'],grant_id=grant['id'],reason=body['reason'],created_by=self.actor,created_at=now()))
            state={'reject':'rejected','request_changes':'changes_requested','approve':'submitted'}[body['verdict']]
            if state=='submitted' and len(self.latest_approvals(p,r))==2:state='approved'
            self.db.execute(T['proposals'].update().where(T['proposals'].c.id==pid).values(state=state,updated_at=now()))
            return dict(command_id=cid,status='completed',resource_id=rid,task_id=None),pid,r['content_hash'],r['content_hash']
        return self.command('proposal.review',pid,key,body,permission,domain,action)

    def latest_approvals(self,p,r):
        t=T['review_decisions'];result={}
        rows=self.db.execute(sa.select(t).where(t.c.proposal_id==p['id'],t.c.revision==p['current_revision']).order_by(t.c.created_at.desc(),t.c.id.desc())).mappings()
        seen=set()
        for row in rows:
            if row['scope'] in seen:continue
            seen.add(row['scope'])
            if row['verdict']!='approve' or row['content_hash']!=r['content_hash'] or row['dependency_hash']!=r['dependency_hash']:continue
            grant=self.row('role_grants',T['role_grants'].c.id==row['grant_id'])
            if not grant or grant['revoked_at'] or (grant['expires_at'] and utc(grant['expires_at'])<=now()):continue
            permission=p['domain']+'.review' if row['scope']=='content' else 'source.rights_review'
            if grant['user_id']!=row['created_by'] or p['domain'] not in grant['domain_scope'] or permission not in POLICY['role_presets'][grant['role']]:continue
            if row['created_by'] in p['contributor_ids']:continue
            result[row['scope']]=dict(row)
        return result

    def publish(self,key,body):
        body=validate('PublicationBuild',body);domain=body['domain']
        def action(cid):
            if self.db.bind.dialect.name=='postgresql':
                self.db.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),{'scope':'management:publish:'+domain})
            refs=body['proposal_refs']
            if len({x['proposal_id'] for x in refs})!=len(refs):fail('DUPLICATE_PROPOSAL')
            members={};pins=None;decisions=[];modified=set()
            if body['base_publication_id']:
                base=self.row('publications',T['publications'].c.id==body['base_publication_id'])
                if not base or base['domain']!=domain:fail('DEPENDENCY_CHANGED')
                members={x['logical_id']:x for x in copy.deepcopy(base['manifest']['members'])}
            for reference in sorted(refs,key=lambda x:x['proposal_id']):
                p=self.proposal(reference['proposal_id'],True);r=self.revision(p)
                if p['domain']!=domain:fail('FORBIDDEN',status=403)
                if self.actor in p['contributor_ids']:fail('SELF_PUBLISH_FORBIDDEN')
                if p['state']!='approved' or reference['revision']!=p['current_revision'] or reference['content_hash']!=r['content_hash']:fail('APPROVAL_STALE')
                if r['dependency_pins']['base_publication_id']!=body['base_publication_id']:fail('DEPENDENCY_CHANGED')
                if pins is not None and pins!=r['dependency_pins']:fail('DEPENDENCY_CHANGED')
                pins=r['dependency_pins'];self.validation(p,r,reference['validation_id'])
                approvals=self.latest_approvals(p,r)
                for scope,field in [('content','content_approval_id'),('source_rights','source_rights_approval_id')]:
                    if scope not in approvals or approvals[scope]['id']!=reference[field] or approvals[scope]['validation_id']!=reference['validation_id']:fail('APPROVAL_STALE')
                    decisions.append(reference[field])
                if p['logical_id'] in modified:fail('DUPLICATE_LOGICAL_ID')
                modified.add(p['logical_id'])
                t=T['entity_versions'];condition=sa.and_(t.c.domain==domain,t.c.logical_id==p['logical_id'],t.c.content_hash==r['content_hash'],t.c.dependency_hash==r['dependency_hash'])
                entity=self.row('entity_versions',condition)
                if entity is None:
                    version_id=ident()
                    self.db.execute(t.insert().values(id=version_id,domain=domain,logical_id=p['logical_id'],proposal_id=p['id'],revision=p['current_revision'],
                        content=r['content'],content_hash=r['content_hash'],dependency_hash=r['dependency_hash'],created_at=now()))
                else:
                    if entity['content']!=r['content']:fail('HASH_COLLISION')
                    version_id=entity['id']
                members[p['logical_id']]={'logical_id':p['logical_id'],'ref':{'kind':'managed','version_id':version_id,'content_hash':r['content_hash']}}
                self.db.execute(T['proposals'].update().where(T['proposals'].c.id==p['id']).values(state='published',updated_at=now()))
            if body['base_publication_id'] is None and pins['legacy_recipe_release_id'] and domain in ('recipe','ingredient'):
                from app.models.recipe import Recipe,IngredientAlias
                model=Recipe if domain=='recipe' else IngredientAlias
                for entity in self.db.scalars(sa.select(model).where(model.recipe_release_id==pins['legacy_recipe_release_id'])):
                    logical=entity.recipe_id if domain=='recipe' else entity.food_id
                    members.setdefault(logical,{'logical_id':logical,'ref':{'kind':'legacy','legacy_recipe_release_id':pins['legacy_recipe_release_id'],
                        'logical_id':logical,'captured_content_hash':pins['legacy_db_capture_sha256']}})
            manifest=dict(contract_version=VERSION,domain=domain,dependency_pins=pins,members=[members[k] for k in sorted(members)],
                review_decision_ids=sorted(decisions),nutrition_readiness='not_inherited',formal_generation_ready=False)
            validate('PublicationManifest',manifest)
            pubid=ident();created=now();mh=digest('manifest',manifest)
            self.db.execute(T['publications'].insert().values(id=pubid,domain=domain,base_publication_id=body['base_publication_id'],manifest=manifest,manifest_hash=mh,created_by=self.actor,created_at=created))
            for member in manifest['members']:
                self.db.execute(T['publication_members'].insert().values(publication_id=pubid,logical_id=member['logical_id'],version_id=member['ref'].get('version_id'),ref=member['ref']))
            result=dict(publication_id=pubid,domain=domain,manifest_hash=mh,base_publication_id=body['base_publication_id'],member_count=len(members),
                created_at=stamp(created),created_by=self.actor,formal_generation_ready=False)
            return result,pubid,None,mh
        return self.command('publication.build',domain,key,body,domain+'.publish',domain,action)

    def activation_plan(self,key,channel,body):
        body=validate('ActivationPlanRequest',body)
        domain={'recipe_catalog':'recipe','ingredient_catalog':'ingredient','price_policy':'price','source_policy':'source_policy'}.get(channel)
        if not domain:fail('NOT_FOUND',status=404)
        def action(cid):
            binding=self.row('active_bindings',T['active_bindings'].c.channel==channel,True)
            publication=self.row('publications',T['publications'].c.id==body['publication_id'])
            if not binding or binding['revision']!=body['expected_binding_revision']:fail('BINDING_CHANGED')
            if not publication or publication['domain']!=domain:fail('NOT_FOUND',status=404)
            result=dict(plan_id=cid,publication_id=publication['id'],channel=channel,expected_binding_revision=binding['revision'],expires_at=stamp(now()+timedelta(minutes=5)),
                can_activate=False,issues=[dict(code='CONSUMER_ADAPTER_NOT_READY',severity='blocking',json_pointer='',message='尚未啟用受測 consumer adapter。')])
            result['plan_hash']=digest('plan',result)
            return result,channel,None,publication['manifest_hash']
        return self.command('binding.plan',channel,key,body,domain+'.activate',domain,action)

    def activate(self,key,channel,body,rollback=False):
        body=validate('RollbackCommand' if rollback else 'ActivationCommand',body)
        domain={'recipe_catalog':'recipe','ingredient_catalog':'ingredient','price_policy':'price','source_policy':'source_policy'}.get(channel)
        if not domain:fail('NOT_FOUND',status=404)
        self.access_lock();self.require(domain+'.activate',domain)
        # Deliberate fail-closed boundary; no feature switch can bypass this gate.
        fail('CONSUMER_ADAPTER_NOT_READY')

def legacy_capture(session):
    """Capture DB content, never certify workbook bytes or inspect T04 approvals."""
    names=('recipe_data_releases','source_archive','ingredient_aliases','allergens','recipes','recipe_steps','recipe_ingredients','recipe_covers')
    parts=[]
    for name in names:
        if session.bind.dialect.name=='postgresql':
            rows=session.scalars(sa.text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY to_jsonb(t)::text')).all()
        else:
            from app.db.base import Base
            table=Base.metadata.tables[name]
            def encode(v):
                if isinstance(v,Decimal):return str(v)
                if isinstance(v,datetime):return stamp(v)
                if isinstance(v,bytes):return hashlib.sha256(v).hexdigest()
                return v
            rows=sorted(__import__('json').dumps({k:encode(v) for k,v in row.items()},sort_keys=True,ensure_ascii=False,default=str) for row in session.execute(sa.select(table)).mappings())
        parts.append(name+'\n'+'\n'.join(rows))
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()
