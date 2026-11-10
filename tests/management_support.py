"""Bounded reconciliation rehearsal for disposable management test databases.

This is not a production adoption CLI. Unknown schemas fail before any DDL.
The expected fingerprints must come from an independent reference fixture.
"""
from datetime import datetime, timezone
import sqlalchemy as sa
from alembic import command
from app.management.contracts import digest
from app.management.legacy_schema import cfg, data_hash, fingerprint
from app.management.models import tables as T

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
    command.upgrade(config,'m1107management1')
    if data_hash(connection,exclude=('meal_menu_rejections',))!=protected:raise ValueError('Protected legacy data changed')
    plan=digest('adoption-plan',dict(old=old,before=expected_before,after=expected_after))
    connection.execute(T['schema_adoptions'].insert().values(id=__import__('uuid').uuid4().hex,plan_hash=plan,old_metadata={'revision':old,'source':'synthetic_observed_shape_not_recovered_graph'},
        before_fingerprint=before,after_fingerprint=expected_after,data_hash=protected,approver=approver,backup_reference=backup_reference,created_at=datetime.now(timezone.utc)))
    return {'old_revision':old,'new_revision':connection.scalars(sa.text('SELECT version_num FROM alembic_version')).all(),
            'before':before,'after':fingerprint(connection),'protected_before':protected,'plan_hash':plan}
