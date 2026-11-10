"""Management workflows in marker-verified disposable PostgreSQL databases."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import Mock
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from alembic import command
from app.db.models import User,Household,AdoptedMealPlan
from app.models.recipe import IngredientAlias
from app.models.nutrition import NutritionImport,NutritionSource,FoodNutrition,NutritionFoodMapping
from app.management.models import tables as T
from app.management.service import Management,now
from management_support import cfg,fingerprint,data_hash,rehearse_adoption,assert_disposable,MARKER,LEGACY_DB
from app.api.errors import ApiError
from test_management_foundation import Environment,ACTORS

ROOT=Path(__file__).resolve().parents[1]
URL=os.getenv('MANAGEMENT_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Dedicated management test database URL not set')

def engine_for(name):
    url=sa.engine.make_url(URL).set(database=name)
    assert name in ['meal_planner_1102_v3_management_test',LEGACY_DB,'meal_planner_1102_v3_management_reference_test']
    engine=sa.create_engine(url)
    with engine.connect() as c:
        assert c.scalar(sa.text('SELECT current_database()'))==name
        assert c.scalar(sa.text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))==MARKER
    return engine

def clean(engine,head='head'):
    # Database and marker were verified above; never use generic TEST_DATABASE_URL.
    with engine.begin() as c:
        c.execute(sa.text('DROP SCHEMA public CASCADE'))
        c.execute(sa.text('CREATE SCHEMA public AUTHORIZATION postgres'))
        c.execute(sa.text('GRANT USAGE ON SCHEMA public TO PUBLIC'))
        command.upgrade(cfg(c),head)

def users(engine):
    with Session(engine) as db,db.begin():
        db.add_all([User(id=uuid.UUID(a),email='fixture'+str(i)+'@example.invalid',password_hash='synthetic-only') for i,a in enumerate(ACTORS)])

@pytest.fixture
def pg_env():
    engine=engine_for('meal_planner_1102_v3_management_test');clean(engine);users(engine)
    e=Environment(engine);e.call(ACTORS[0],'bootstrap','synthetic pg bootstrap')
    e.grants(ACTORS[1],['source_reviewer']);e.grants(ACTORS[2],['source_reviewer','publisher'])
    yield e
    engine.dispose()

def legacy_tables(c):
    for sql in (ROOT/'tests/fixtures/management/legacy-admin.sql').read_text(encoding='utf8').split(';'):
        if sql.strip():c.exec_driver_sql(sql)

def protected_fixtures(engine):
    users(engine)
    with Session(engine) as db,db.begin():
        household=Household(id=uuid.uuid4(),name='synthetic legacy household')
        db.add(household);db.flush()
        db.add(AdoptedMealPlan(id=uuid.uuid4(),household_id=household.id,meal_date=date(2026,11,7),meal_type='breakfast',mode='database_menu',
            status='adopted',version=1,dishes=[{'fixture':'old dish'}],snapshot={'fixture':'immutable adopted snapshot'},adopted_at=now()))
        db.add(IngredientAlias(food_id='FOOD_FIXTURE',recipe_release_id='legacy-1017',canonical_name='synthetic food',aliases=[],ambiguous_aliases=[],review_status='pending',quality_issues=[]));db.flush()
        db.add(NutritionImport(import_id='synthetic-import',source_files={'fixture':True},report={},imported_at=now()));db.flush()
        for i,status in enumerate(['approved','rejected']):
            sid='synthetic-source-'+str(i);nid='synthetic-nutrition-'+str(i)
            db.add(NutritionSource(source_id=sid,import_id='synthetic-import',source_key=sid,provider='fixture',source_record_id=sid,source_sheet='fixture',source_row=i+1,raw_data={'fixture':True}))
            db.add(FoodNutrition(nutrition_id=nid,import_id='synthetic-import',food_id='FOOD_FIXTURE',source_row=i+1,standard_name='fixture',cooking_state='raw',nutrition_complete=False,quality_issues=['synthetic'],raw_data={'unknown_is_null':None}));db.flush()
            db.add(NutritionFoodMapping(mapping_id='synthetic-map-'+str(i),import_id='synthetic-import',recipe_release_id='legacy-1017',food_id='FOOD_FIXTURE',nutrition_id=nid,source_id=sid,cooking_state='raw',approval_status=status,mapping_version='synthetic',evidence={'fixture':True}))
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO admin_recipe_proposals VALUES ('legacy-proposal',1,'approved','unmapped-legacy-author','unmapped-legacy-reviewer','legacy-1017','{}','old-content-hash','old-approved-hash','old-dependency-hash',NULL,now())"))
        c.execute(sa.text("INSERT INTO admin_review_events VALUES ('legacy-review','legacy-proposal','unmapped-legacy-reviewer','approve','{}',now())"))
        c.execute(sa.text("INSERT INTO admin_schedule_runs VALUES ('legacy-run','legacy-job','succeeded',now(),now(),'synthetic legacy history')"))
        c.execute(sa.text("INSERT INTO admin_scheduler_heartbeat VALUES ('legacy-heartbeat',now(),'[]')"))

def test_empty_main_upgrade_downgrade_and_no_automatic_grants():
    engine=engine_for('meal_planner_1102_v3_management_reference_test');clean(engine)
    with engine.begin() as c:
        assert c.scalar(sa.text('SELECT count(*) FROM management_role_grants'))==0
        assert c.scalar(sa.text('SELECT count(*) FROM management_publications'))==0
        assert c.scalar(sa.text('SELECT count(*) FROM management_active_bindings WHERE publication_id IS NOT NULL'))==0
        command.downgrade(cfg(c),'d1105merge1')
        assert not sa.inspect(c).has_table('management_publications')
        command.upgrade(cfg(c),'head')
        assert c.scalar(sa.text('SELECT version_num FROM alembic_version'))=='m1109mealflow1'
    engine.dispose()

def test_synthetic_legacy_reconciliation_preserves_snapshots_and_t04(tmp_path):
    reference=engine_for('meal_planner_1102_v3_management_reference_test');clean(reference,'d1105merge1')
    with reference.begin() as c:
        legacy_tables(c);after=fingerprint(c)
        c.execute(sa.text('DROP TABLE meal_menu_rejections'));before=fingerprint(c)
    legacy=engine_for(LEGACY_DB);clean(legacy,'d1105merge1')
    with legacy.begin() as c:
        legacy_tables(c);c.execute(sa.text('DROP TABLE meal_menu_rejections'));c.execute(sa.text("UPDATE alembic_version SET version_num='d1105admin2'"))
    protected_fixtures(legacy)
    with legacy.connect() as c:old=data_hash(c,exclude=('meal_menu_rejections',))
    with legacy.begin() as c:
        report=rehearse_adoption(c,before,after,'synthetic-test-reviewer','synthetic-only transaction rollback fixture')
    with legacy.connect() as c:
        assert report['new_revision']==['m1107management1'] and report['after']==after
        assert data_hash(c,exclude=('meal_menu_rejections',))==old
        assert c.execute(sa.text('SELECT approval_status,count(*) FROM nutrition_food_mappings GROUP BY approval_status ORDER BY approval_status')).all()==[('approved',1),('rejected',1)]
        assert c.scalar(sa.text('SELECT count(*) FROM management_role_grants'))==0
        assert c.scalar(sa.text('SELECT count(*) FROM management_review_decisions'))==0
        assert c.scalar(sa.text('SELECT count(*) FROM management_publications'))==0
        assert c.scalar(sa.text("SELECT status FROM admin_recipe_proposals WHERE id='legacy-proposal'"))=='approved'
        assert c.scalar(sa.text('SELECT count(*) FROM management_schema_adoptions'))==1
    with legacy.begin() as c:
        command.upgrade(cfg(c), 'head')
        command.check(cfg(c))
    (tmp_path/'synthetic-adoption-result.json').write_text(__import__('json').dumps(report,indent=2),encoding='utf8')
    reference.dispose();legacy.dispose()

def test_legacy_mismatch_and_post_repair_failure_leave_metadata_unchanged():
    reference=engine_for('meal_planner_1102_v3_management_reference_test');clean(reference,'d1105merge1')
    with reference.begin() as c:
        legacy_tables(c);after=fingerprint(c)
        c.execute(sa.text('DROP TABLE meal_menu_rejections'));before=fingerprint(c)
    legacy=engine_for(LEGACY_DB);clean(legacy,'d1105merge1')
    with legacy.begin() as c:
        legacy_tables(c);c.execute(sa.text('DROP TABLE meal_menu_rejections'));c.execute(sa.text("UPDATE alembic_version SET version_num='d1105admin2'"))
    for expected_before,expected_after in [('0'*64,after),(before,'0'*64)]:
        with legacy.begin() as c:
            with pytest.raises(ValueError):rehearse_adoption(c,expected_before,expected_after,'fixture','fixture')
            assert c.scalar(sa.text('SELECT version_num FROM alembic_version'))=='d1105admin2'
            assert not sa.inspect(c).has_table('meal_menu_rejections')
            assert not sa.inspect(c).has_table('management_schema_adoptions')
    with legacy.begin() as c:
        c.execute(sa.text('CREATE INDEX unexpected_legacy_index ON admin_review_events(actor)'))
        with pytest.raises(ValueError,match='fingerprint'):rehearse_adoption(c,before,after,'fixture','fixture')
        assert c.scalar(sa.text('SELECT version_num FROM alembic_version'))=='d1105admin2'
    reference.dispose();legacy.dispose()

def test_production_and_agent_databases_rejected_before_sql():
    for url in ['postgresql://postgres@localhost/meal_planner_1102_v3','postgresql://postgres@localhost/meal_planner_1102_v3_t01_v4_test']:
        conn=Mock();conn.engine.url=sa.engine.make_url(url)
        with pytest.raises(ValueError):assert_disposable(conn)
        conn.execute.assert_not_called();conn.scalar.assert_not_called()

def test_postgres_full_flow_and_immutable_database_guards(pg_env):
    e=pg_env;p=e.create();pub=e.call(ACTORS[2],'publish',e.key(),e.approved(p))
    with e.engine.begin() as c:
        for table in ['artifacts','review_decisions','publications','audit_events']:
            with pytest.raises(sa.exc.DatabaseError):
                with c.begin_nested():c.execute(sa.text('DELETE FROM management_'+table))
        with pytest.raises(RuntimeError,match='data exists'):
            with c.begin_nested():command.downgrade(cfg(c),'d1105merge1')
        assert c.scalar(sa.select(sa.func.count()).select_from(T['publications']))==1

def test_concurrent_same_key_publishes_once(pg_env):
    e=pg_env;p=e.create();body=e.approved(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:e.call(ACTORS[2],'publish','same-publication-key',body),range(2)))
    assert results[0]==results[1]
    with e.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(T['publications']))==1
        assert c.scalar(sa.select(sa.func.count()).select_from(T['audit_events']).where(T['audit_events'].c.action=='publication.build'))==1

def test_concurrent_revision_only_one_cas_succeeds(pg_env):
    e=pg_env;p=e.create();body=dict(**e.expected(p),domain=p['domain'],content=p['content'],dependency_pins=p['dependency_pins'])
    def edit(i):
        try:return e.call(ACTORS[1],'revise','edit-key-'+str(i),p['proposal_id'],body)['revision']
        except ApiError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,range(2)))
    assert sorted(map(str,results))==['2','VERSION_CONFLICT']

def test_management_http_requires_restricted_db_writer(pg_env,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.management import router
    from app.api.deps import current_auth
    from app.api.errors import register_error_handlers
    from app.core.config import settings
    from app.db.session import get_db
    role='mgmt_fixture_'+uuid.uuid4().hex[:12]
    engine=pg_env.engine
    with engine.begin() as c:
        c.exec_driver_sql('CREATE ROLE '+role+' NOLOGIN')
        c.exec_driver_sql('GRANT USAGE ON SCHEMA public TO '+role)
        c.exec_driver_sql('GRANT SELECT ON management_access_state, management_role_grants TO '+role)
    try:
        monkeypatch.setattr(settings,'management_enabled',True)
        app=FastAPI();register_error_handlers(app);app.include_router(router)
        app.dependency_overrides[current_auth]=lambda:SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))
        def session(under_role=False):
            with Session(engine) as db:
                if under_role:db.execute(sa.text('SET LOCAL ROLE '+role))
                yield db
        app.dependency_overrides[get_db]=session
        with TestClient(app) as client:
            assert client.get('/admin/v1/capabilities').json()['error']['code']=='MANAGEMENT_WRITER_PRIVILEGES_UNSAFE'
            def restricted():yield from session(True)
            app.dependency_overrides[get_db]=restricted
            result=client.get('/admin/v1/capabilities')
            assert result.status_code==200 and result.json()['external_execution_enabled'] is False
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP OWNED BY '+role)
            c.exec_driver_sql('DROP ROLE '+role)
