"""Management service safety checks: synthetic SQLite, no external services."""
import copy
import json
import uuid
from pathlib import Path
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from app.api.errors import ApiError
from app.db.models import User
from app.management.models import tables as T
from app.management.schema_v1 import install_guards
from app.management.service import Management, now
from app.management.contracts import rules_pin, validate, digest

ACTORS=[str(uuid.uuid5(uuid.NAMESPACE_DNS,'management-fixture-'+str(i))) for i in range(1,6)]

class Environment:
    def __init__(self,engine):self.engine=engine;self.index=0
    def key(self):self.index+=1;return 'test-'+str(self.index)
    def call(self,actor,method,*args,**kwargs):
        with Session(self.engine) as db,db.begin():return getattr(Management(db,actor),method)(*args,**kwargs)
    def grants(self,actor,roles,scope=None):
        for role in roles:
            revision=self.call(ACTORS[0],'capabilities')['access_revision']
            self.call(ACTORS[0],'grant',self.key(),dict(user_id=actor,role=role,domain_scope=scope or ['source_policy'],expires_at=None,expected_access_revision=revision,reason='synthetic role grant'))
    def create(self):
        artifact=self.call(ACTORS[1],'upload',self.key(),'source_policy','manual_entry','text/plain',b'synthetic source rights evidence','fixture')
        content=dict(provider='pxmart',external_execution_enabled=False,execution_mode='fixtures_only',registered_endpoint_key='pxmart-fixture',minimum_interval_seconds=2,timeout_seconds=15,max_retries=1,freshness_seconds=86400,
            sources=[dict(artifact_id=artifact['artifact_id'],artifact_sha256=artifact['sha256'],origin_kind='manual_entry',source_policy_version_id=None)])
        pins=dict(legacy_recipe_release_id=None,legacy_recipe_source_sha256=None,legacy_db_capture_sha256=None,base_publication_id=None,base_publication_manifest_sha256=None,
            ingredient_publication_id=None,ingredient_manifest_sha256=None,source_policy_publication_id=None,source_policy_manifest_sha256=None,contract_version='management-v1.0.0-rc1',**rules_pin())
        body=dict(domain='source_policy',content=content,dependency_pins=pins,reason='synthetic proposal')
        return self.call(ACTORS[1],'create',self.key(),body)
    def expected(self,p):return dict(expected_revision=p['revision'],expected_content_hash=p['content_hash'],reason='synthetic operation')
    def submit(self,p):
        receipt=self.call(ACTORS[1],'request_validation',self.key(),p['proposal_id'],self.expected(p))
        result=self.call(ACTORS[1],'run_validation',receipt['task_id'])
        assert result['catalog_ready'] and result['formal_generation_ready'] is False
        self.call(ACTORS[1],'submit',self.key(),p['proposal_id'],self.expected(p))
        return result
    def approved(self,p):
        validation=self.submit(p);ids={}
        for scope in ['content','source_rights']:
            body=dict(**self.expected(p),validation_id=validation['validation_id'],dependency_hash=p['dependency_hash'],scope=scope,verdict='approve')
            ids[scope]=self.call(ACTORS[2],'review',self.key(),p['proposal_id'],body)['resource_id']
        reference=dict(proposal_id=p['proposal_id'],revision=p['revision'],content_hash=p['content_hash'],validation_id=validation['validation_id'],content_approval_id=ids['content'],source_rights_approval_id=ids['source_rights'])
        return dict(domain=p['domain'],base_publication_id=None,proposal_refs=[reference],reason='synthetic publication')

@pytest.fixture
def env(tmp_path):
    engine=sa.create_engine('sqlite:///'+str(tmp_path/'management.sqlite'))
    @sa.event.listens_for(engine,'connect')
    def connect(dbapi,_):dbapi.isolation_level=None;dbapi.execute('PRAGMA foreign_keys=ON')
    @sa.event.listens_for(engine,'begin')
    def begin(c):c.exec_driver_sql('BEGIN')
    User.metadata.create_all(engine,tables=[User.__table__,*T.values()])
    with engine.begin() as c:
        c.execute(T['access_state'].insert().values(id='global',revision=1))
        for channel in ['source_policy','recipe_catalog','ingredient_catalog','price_policy']:
            c.execute(T['active_bindings'].insert().values(channel=channel,revision=1,publication_id=None,updated_by=None,updated_at=now()))
        install_guards(c)
    with Session(engine) as db,db.begin():
        db.add_all([User(id=uuid.UUID(a),email='fixture'+str(i)+'@example.invalid',password_hash='synthetic-not-a-password') for i,a in enumerate(ACTORS)])
    e=Environment(engine);e.call(ACTORS[0],'bootstrap','synthetic offline bootstrap')
    e.grants(ACTORS[1],['source_reviewer'],['recipe','ingredient','price','source_policy'])
    e.grants(ACTORS[2],['source_reviewer','publisher'],['recipe','ingredient','price','source_policy'])
    yield e
    engine.dispose()

def code(expected,fn):
    with pytest.raises(ApiError) as exc:fn()
    assert exc.value.code==expected

def test_full_publication_is_immutable_not_active_and_idempotent(env):
    p=env.create();body=env.approved(p);key=env.key()
    result=env.call(ACTORS[2],'publish',key,body)
    assert env.call(ACTORS[2],'publish',key,body)==result
    with env.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(T['publications']))==1
        assert c.scalar(sa.select(T['active_bindings'].c.publication_id).where(T['active_bindings'].c.channel=='source_policy')) is None
        manifest=c.scalar(sa.select(T['publications'].c.manifest))
        assert manifest['nutrition_readiness']=='not_inherited' and not manifest['formal_generation_ready']
        validate('PublicationManifest',manifest)
    with env.engine.begin() as c:
        with pytest.raises(sa.exc.DatabaseError):c.execute(T['publications'].update().values(manifest={}))

def test_self_review_and_self_publish_are_rejected(env):
    p=env.create();v=env.submit(p)
    body=dict(**env.expected(p),validation_id=v['validation_id'],dependency_hash=p['dependency_hash'],scope='content',verdict='approve')
    code('SELF_REVIEW_FORBIDDEN',lambda:env.call(ACTORS[1],'review',env.key(),p['proposal_id'],body))
    # A second proposal goes through independent review; adding publisher does not
    # let its contributor publish it.
    p2=env.create();publication=env.approved(p2);env.grants(ACTORS[1],['publisher'])
    code('SELF_PUBLISH_FORBIDDEN',lambda:env.call(ACTORS[1],'publish',env.key(),publication))

def test_withdrawn_proposal_cannot_be_resubmitted(env):
    p=env.create();env.call(ACTORS[1],'withdraw',env.key(),p['proposal_id'],env.expected(p))
    code('INVALID_STATE',lambda:env.call(ACTORS[1],'submit',env.key(),p['proposal_id'],env.expected(p)))

def test_partial_approval_cannot_publish(env):
    p=env.create();v=env.submit(p)
    b=dict(**env.expected(p),validation_id=v['validation_id'],dependency_hash=p['dependency_hash'],scope='content',verdict='approve')
    decision=env.call(ACTORS[2],'review',env.key(),p['proposal_id'],b)
    body=dict(domain='source_policy',base_publication_id=None,proposal_refs=[dict(proposal_id=p['proposal_id'],revision=1,content_hash=p['content_hash'],validation_id=v['validation_id'],content_approval_id=decision['resource_id'],source_rights_approval_id=decision['resource_id'])],reason='bad partial publish')
    code('APPROVAL_STALE',lambda:env.call(ACTORS[2],'publish',env.key(),body))

def test_revision_invalidates_approval_and_stale_clients(env):
    p=env.create();body=env.approved(p)
    revised=dict(**env.expected(p),domain=p['domain'],content=copy.deepcopy(p['content']),dependency_pins=p['dependency_pins'])
    revised['content']['timeout_seconds']=20
    new=env.call(ACTORS[1],'revise',env.key(),p['proposal_id'],revised)
    assert new['revision']==2 and new['status']=='draft'
    code('VERSION_CONFLICT',lambda:env.call(ACTORS[1],'revise',env.key(),p['proposal_id'],revised))
    code('APPROVAL_STALE',lambda:env.call(ACTORS[2],'publish',env.key(),body))

def test_no_permission_or_wrong_domain(env):
    code('FORBIDDEN',lambda:env.call(ACTORS[3],'capabilities'))
    env.grants(ACTORS[3],['source_reviewer'],['recipe'])
    p=env.create()
    code('FORBIDDEN',lambda:env.call(ACTORS[3],'submit',env.key(),p['proposal_id'],env.expected(p)))

def test_revoke_reviewer_blocks_publication(env):
    p=env.create();body=env.approved(p)
    with env.engine.connect() as c:
        gid=c.scalar(sa.select(T['role_grants'].c.id).where(T['role_grants'].c.user_id==ACTORS[2],T['role_grants'].c.role=='source_reviewer'))
    revision=env.call(ACTORS[0],'capabilities')['access_revision']
    env.call(ACTORS[0],'revoke',env.key(),gid,dict(expected_access_revision=revision,reason='synthetic revocation'))
    code('APPROVAL_STALE',lambda:env.call(ACTORS[2],'publish',env.key(),body))

def test_validation_executor_rechecks_grants_and_revision(env):
    p=env.create();task=env.call(ACTORS[1],'request_validation',env.key(),p['proposal_id'],env.expected(p))
    b=dict(**env.expected(p),domain=p['domain'],content=p['content'],dependency_pins=p['dependency_pins'])
    env.call(ACTORS[1],'revise',env.key(),p['proposal_id'],b)
    code('VERSION_CONFLICT',lambda:env.call(ACTORS[1],'run_validation',task['task_id']))

def test_idempotency_conflict_has_no_new_audit(env):
    p=env.create();b=env.expected(p);key=env.key()
    env.call(ACTORS[1],'request_validation',key,p['proposal_id'],b)
    code('IDEMPOTENCY_CONFLICT',lambda:env.call(ACTORS[1],'request_validation',key,p['proposal_id'],{**b,'reason':'different'}))
    with env.engine.connect() as c:assert c.scalar(sa.select(sa.func.count()).select_from(T['tasks']))==1

def test_audit_failure_rolls_back_mutation_and_receipt(env):
    p=env.create()
    with env.engine.begin() as c:
        c.exec_driver_sql("CREATE TRIGGER reject_audit BEFORE INSERT ON management_audit_events WHEN NEW.action='proposal.revise' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    b=dict(**env.expected(p),domain=p['domain'],content=p['content'],dependency_pins=p['dependency_pins'])
    with pytest.raises(sa.exc.DatabaseError):env.call(ACTORS[1],'revise',env.key(),p['proposal_id'],b)
    assert env.call(ACTORS[1],'get',p['proposal_id'])['revision']==1
    with env.engine.connect() as c:assert c.scalar(sa.select(sa.func.count()).select_from(T['commands']).where(T['commands'].c.operation=='proposal.revise'))==0

def test_no_activation_without_adapter(env):
    p=env.create();pub=env.call(ACTORS[2],'publish',env.key(),env.approved(p))
    plan=env.call(ACTORS[2],'activation_plan',env.key(),'source_policy',dict(publication_id=pub['publication_id'],expected_binding_revision=1,reason='synthetic plan'))
    assert not plan['can_activate']
    code('CONSUMER_ADAPTER_NOT_READY',lambda:env.call(ACTORS[2],'activate',env.key(),'source_policy',dict(publication_id=pub['publication_id'],expected_binding_revision=1,plan_id=plan['plan_id'],plan_hash=plan['plan_hash'],reason='must not activate')))

def test_artifact_and_review_evidence_cannot_be_updated(env):
    p=env.create();env.approved(p)
    for name in ['artifacts','proposal_revisions','review_decisions','validation_runs']:
        with env.engine.begin() as c:
            with pytest.raises(sa.exc.DatabaseError):c.execute(T[name].delete())

def test_unlisted_manifest_member_is_rejected(env):
    p=env.create();pub=env.call(ACTORS[2],'publish',env.key(),env.approved(p))
    with env.engine.begin() as c:
        with pytest.raises(sa.exc.DatabaseError):c.execute(T['publication_members'].insert().values(publication_id=pub['publication_id'],logical_id='injected',version_id=None,ref={'kind':'legacy'}))

def test_client_authority_fields_and_live_execution_rejected():
    examples=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/management/rc1-examples.json').read_text(encoding='utf8'))['cases']
    for case in examples:
        if case['valid']:validate(case['schema'],case['value'])
        else:code('INVALID_INPUT',lambda c=case:validate(c['schema'],c['value']))

def test_packaged_contracts_match_frozen_rc1():
    root=Path(__file__).resolve().parents[1]
    for name in ['schemas.json','permissions.json','workflow.json']:
        assert __import__('hashlib').sha256(json.dumps(json.loads((root/'backend/app/management/contracts'/name).read_text(encoding='utf8')),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()==json.loads((root/'tests/fixtures/management/rc1-contract-sha256.json').read_text(encoding='utf8'))[name]

@pytest.mark.parametrize('count,basis,name',[('950','portions','番茄配方'),('1','batch','蕃茄配方')])
def test_recipe_catalog_publication_preserves_mass_basis_and_unknown_nutrition(env,count,basis,name):
    from app.models.recipe import RecipeDataRelease,SourceArchive,IngredientAlias,Allergen,Recipe,RecipeStep,RecipeIngredient,RecipeCover
    from app.management.service import legacy_capture
    User.metadata.create_all(env.engine,tables=[x.__table__ for x in [RecipeDataRelease,SourceArchive,IngredientAlias,Allergen,Recipe,RecipeStep,RecipeIngredient,RecipeCover]])
    with Session(env.engine) as db,db.begin():
        db.add(RecipeDataRelease(release_id='synthetic-base',dataset_version='synthetic',source_sha256='a'*64,schema_version='fixture',source_kind='xlsx_import',row_counts={},active=True));db.flush()
        db.add(IngredientAlias(food_id='FOOD_FIXTURE',recipe_release_id='synthetic-base',canonical_name='合成食材',aliases=[],ambiguous_aliases=[],review_status='pending',quality_issues=[]))
    env.grants(ACTORS[1],['recipe_editor'],['recipe']);env.grants(ACTORS[2],['recipe_reviewer'],['recipe'])
    artifact=env.call(ACTORS[1],'upload',env.key(),'recipe','manual_entry','text/plain',b'synthetic recipe source','fixture')
    examples=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/management/rc1-examples.json').read_text(encoding='utf8'))['cases']
    body=copy.deepcopy(next(x['value'] for x in examples if x['id']=='recipe_950_portions'))
    body['content'].update(logical_recipe_id=None,name=name)
    body['content']['servings'].update(count=count,basis=basis,raw_text=count+('批' if basis=='batch' else '人份'),evidence_ids=[artifact['artifact_id']])
    line=body['content']['ingredients'][0];line['food_id']='FOOD_FIXTURE'
    for field in ['amount','weight_g']:line[field]['evidence_ids']=[artifact['artifact_id']]
    body['content']['sources']=[dict(artifact_id=artifact['artifact_id'],artifact_sha256=artifact['sha256'],origin_kind='manual_entry',source_policy_version_id=None)]
    with Session(env.engine) as db:capture=legacy_capture(db)
    body['dependency_pins'].update(legacy_recipe_release_id='synthetic-base',legacy_recipe_source_sha256='a'*64,legacy_db_capture_sha256=capture,
        ingredient_publication_id=None,ingredient_manifest_sha256=None,**rules_pin())
    p=env.call(ACTORS[1],'create',env.key(),body)
    publication=env.call(ACTORS[2],'publish',env.key(),env.approved(p))
    with Session(env.engine) as db:
        content=db.scalar(sa.select(T['entity_versions'].c.content))
        assert content['servings']['count']==count and content['servings']['basis']==basis
        assert content['servings']['portions_per_batch'] is None
        assert content['ingredients'][0]['weight_g']['value']=='300'
        assert content['name']==name and 'nutrition' not in content
        assert legacy_capture(db)==capture
    assert publication['formal_generation_ready'] is False
    assert not sa.inspect(env.engine).has_table('nutrition_food_mappings')
