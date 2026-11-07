"""Bounded reconciliation rehearsal for disposable management test databases.

This is not a production adoption CLI. Unknown schemas fail before any DDL.
The expected fingerprints must come from an independent reference fixture.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.management.contracts import digest
from app.management.models import tables as T

ROOT=Path(__file__).resolve().parents[1]
MARKER='overcooked:disposable:management'
LEGACY_DB='meal_planner_1102_v3_management_legacy_test'

def assert_disposable(connection):
    url=connection.engine.url
    if url.database!=LEGACY_DB:
        raise ValueError('Reconciliation is restricted to the dedicated legacy test database')
    if connection.scalar(sa.text('SELECT current_database()'))!=LEGACY_DB:
        raise ValueError('Unexpected database')
    marker=connection.scalar(sa.text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))
    if marker!=MARKER:raise ValueError('Unverified disposable database')

def fingerprint(connection):
    """Include legacy columns, precision, defaults, indexes, constraints and privileges."""
    inspector=sa.inspect(connection);result={}
    for name in sorted(inspector.get_table_names()):
        if name.startswith('management_'):continue
        result[name]={
            'columns':[(c['name'],str(c['type']),c['nullable'],str(c.get('default'))) for c in inspector.get_columns(name)],
            'constraints':sorted(connection.execute(sa.text("SELECT conname,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=:name"),{'name':name}).all()),
            'indexes':sorted(connection.execute(sa.text("SELECT i.relname,pg_get_indexdef(i.oid),x.indisvalid,x.indisready,x.indislive FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=:name"),{'name':name}).all()),
            'grants':sorted(connection.execute(sa.text("SELECT grantee,privilege_type,is_grantable FROM information_schema.role_table_grants WHERE table_schema='public' AND table_name=:name"),{'name':name}).all()),
            'owner_acl':tuple(connection.execute(sa.text("SELECT pg_get_userbyid(t.relowner),t.relacl::text FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=:name"),{'name':name}).one()),
        }
    # Refuse unaccounted triggers/functions/views rather than silently overlooking them.
    for kind,sql in {
        'triggers':"SELECT trigger_name,event_manipulation,event_object_table,action_statement FROM information_schema.triggers WHERE trigger_schema='public' AND event_object_table NOT LIKE 'management_%' ORDER BY 1,2,3",
        'views':"SELECT viewname,definition FROM pg_views WHERE schemaname='public' ORDER BY viewname",
        'functions':"SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname NOT LIKE 'management_%' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e') ORDER BY p.proname",
        'extensions':"SELECT extname,extversion FROM pg_extension ORDER BY extname",
        'sequences':"SELECT sequencename,sequenceowner,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
        'schema_acl':"SELECT pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname='public'",
    }.items():result[kind]=[tuple(r) for r in connection.execute(sa.text(sql))]
    return digest('schema',result)

def data_hash(connection,exclude=()):
    parts={}
    for name in sorted(sa.inspect(connection).get_table_names()):
        if name.startswith('management_') or name=='alembic_version' or name in exclude:continue
        quoted=connection.dialect.identifier_preparer.quote(name)
        parts[name]=connection.scalars(sa.text('SELECT to_jsonb(t)::text FROM '+quoted+' t ORDER BY to_jsonb(t)::text')).all()
    parts['_sequence_values']=[tuple(r) for r in connection.execute(sa.text("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"))]
    return digest('protected-data',parts)

def cfg(connection):
    config=Config(str(ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'migrations'))
    config.attributes.update(connection=connection,database_url=connection.engine.url.render_as_string(hide_password=False),configure_logger=False)
    return config

def rehearse_adoption(connection,expected_before,expected_after,approver,backup_reference):
    assert_disposable(connection)
    with connection.begin_nested():
        return _adopt(connection,expected_before,expected_after,approver,backup_reference)

def _adopt(connection,expected_before,expected_after,approver,backup_reference):
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended('management:reconciliation',0))"))
    old=connection.scalars(sa.text('SELECT version_num FROM alembic_version ORDER BY version_num')).all()
    if old!=['d1105admin2']:raise ValueError('Expected exact synthetic legacy revision')
    before=fingerprint(connection)
    if before!=expected_before:raise ValueError('Legacy schema fingerprint mismatch')
    if not approver or not backup_reference:raise ValueError('Rehearsal approval and backup evidence are required')
    protected=data_hash(connection,exclude=('meal_menu_rejections',))
    # Known missing main DDL only. Every other difference was rejected above.
    connection.execute(sa.text('''CREATE TABLE meal_menu_rejections (
      id uuid PRIMARY KEY, meal_request_id uuid NOT NULL REFERENCES meal_requests(id) ON DELETE CASCADE,
      request_version integer NOT NULL, recipe_ids jsonb NOT NULL,
      created_by uuid REFERENCES users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now())'''))
    connection.execute(sa.text('CREATE INDEX ix_meal_menu_rejections_request ON meal_menu_rejections(meal_request_id,request_version)'))
    if fingerprint(connection)!=expected_after:raise ValueError('Forward reconciliation is not equivalent to reference')
    # Newly created empty table must not change the protected data digest.
    before_new=connection.scalar(sa.text('SELECT count(*) FROM meal_menu_rejections'))
    if before_new:raise ValueError('Unexpected rows in newly created table')
    config=cfg(connection)
    command.stamp(config,'d1105merge1',purge=True)
    command.upgrade(config,'head')
    if data_hash(connection,exclude=('meal_menu_rejections',))!=protected:raise ValueError('Protected legacy data changed')
    plan=digest('adoption-plan',dict(old=old,before=expected_before,after=expected_after))
    connection.execute(T['schema_adoptions'].insert().values(id=__import__('uuid').uuid4().hex,plan_hash=plan,old_metadata={'revision':old,'source':'synthetic_observed_shape_not_recovered_graph'},
        before_fingerprint=before,after_fingerprint=expected_after,data_hash=protected,approver=approver,backup_reference=backup_reference,created_at=datetime.now(timezone.utc)))
    return {'old_revision':old,'new_revision':connection.scalars(sa.text('SELECT version_num FROM alembic_version')).all(),
            'before':before,'after':fingerprint(connection),'protected_before':protected,'plan_hash':plan}
