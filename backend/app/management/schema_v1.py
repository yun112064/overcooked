"""Frozen schema definition for m1107management1. Extend in a new revision."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

DOMAINS = ('recipe', 'ingredient', 'price', 'source_policy')
IMMUTABLE = ('artifacts', 'proposal_revisions', 'validation_runs', 'review_decisions',
             'entity_versions', 'publications', 'publication_members',
             'schedule_revisions', 'audit_events', 'schema_adoptions')

def define_tables(metadata):
    j = sa.JSON().with_variant(JSONB(), 'postgresql')
    uid = sa.Uuid(as_uuid=False)
    if 'users' not in metadata.tables:
        sa.Table('users', metadata, sa.Column('id', uid, primary_key=True))
    tables = {}
    def col(name, typ, *args, **kw): return sa.Column(name, typ, *args, **kw)
    def ident(name='id', **kw): return col(name, sa.String(100), **kw)
    def actor(name='created_by', nullable=False):
        return col(name, uid, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=nullable)
    def time(name='created_at', nullable=False): return col(name, sa.DateTime(timezone=True), nullable=nullable)
    def fk(name, table, **kw): return col(name, sa.String(100), sa.ForeignKey('management_'+table, ondelete='RESTRICT'), **kw)
    def table(name, *cols):
        t = sa.Table('management_'+name, metadata, *cols)
        tables[name] = t
        return t
    table('access_state', ident(primary_key=True), col('revision', sa.Integer, nullable=False), sa.CheckConstraint('revision > 0'))
    table('role_grants', ident(primary_key=True), actor('user_id'), col('role', sa.String(40), nullable=False),
          col('domain_scope', j, nullable=False), time('expires_at', True), time('revoked_at', True),
          col('access_revision', sa.Integer, nullable=False), actor(), time())
    sa.Index('uq_management_current_role', tables['role_grants'].c.user_id, tables['role_grants'].c.role,
             unique=True, postgresql_where=sa.text('revoked_at IS NULL'), sqlite_where=sa.text('revoked_at IS NULL'))
    table('artifacts', ident(primary_key=True), col('domain', sa.String(24), nullable=False),
          col('sha256', sa.String(64), nullable=False), col('media_type', sa.String(100), nullable=False),
          col('byte_count', sa.Integer, nullable=False), col('source_kind', sa.String(32), nullable=False),
          col('payload', sa.LargeBinary, nullable=False), actor(), time(),
          sa.CheckConstraint('byte_count > 0 AND byte_count <= 16777216'))
    table('proposals', ident(primary_key=True), col('domain', sa.String(24), nullable=False),
          ident('logical_id', nullable=False), col('current_revision', sa.Integer, nullable=False),
          col('state', sa.String(24), nullable=False), col('contributor_ids', j, nullable=False), actor(), time(), time('updated_at'),
          sa.CheckConstraint('current_revision > 0'),
          sa.CheckConstraint("state IN ('draft','changes_requested','submitted','approved','rejected','published','withdrawn')"))
    table('proposal_revisions', fk('proposal_id','proposals.id',primary_key=True), col('revision',sa.Integer,primary_key=True),
          col('content',j,nullable=False), col('dependency_pins',j,nullable=False),
          col('content_hash',sa.String(64),nullable=False), col('dependency_hash',sa.String(64),nullable=False), actor(), time())
    revision_fk=lambda: sa.ForeignKeyConstraint(['proposal_id','revision'],
                          ['management_proposal_revisions.proposal_id','management_proposal_revisions.revision'],ondelete='RESTRICT')
    table('validation_runs', ident(primary_key=True), ident('proposal_id',nullable=False), col('revision',sa.Integer,nullable=False),
          col('content_hash',sa.String(64),nullable=False), col('dependency_hash',sa.String(64),nullable=False),
          col('rule_set_version',sa.String(100),nullable=False), col('issues',j,nullable=False),
          col('catalog_ready',sa.Boolean,nullable=False), actor(), time(), revision_fk())
    table('review_decisions', ident(primary_key=True), ident('proposal_id',nullable=False), col('revision',sa.Integer,nullable=False),
          fk('validation_id','validation_runs.id',nullable=False), col('scope',sa.String(24),nullable=False),
          col('verdict',sa.String(24),nullable=False), col('content_hash',sa.String(64),nullable=False),
          col('dependency_hash',sa.String(64),nullable=False), fk('grant_id','role_grants.id',nullable=False),
          col('reason',sa.Text,nullable=False), actor(), time(), revision_fk(),
          sa.CheckConstraint("scope IN ('content','source_rights')"),sa.CheckConstraint("verdict IN ('approve','request_changes','reject')"))
    table('entity_versions', ident(primary_key=True), col('domain',sa.String(24),nullable=False), ident('logical_id',nullable=False),
          ident('proposal_id',nullable=False),col('revision',sa.Integer,nullable=False),col('content',j,nullable=False),
          col('content_hash',sa.String(64),nullable=False),col('dependency_hash',sa.String(64),nullable=False),time(),revision_fk(),
          sa.UniqueConstraint('domain','logical_id','content_hash','dependency_hash',name='uq_management_entity_content'))
    table('publications', ident(primary_key=True),col('domain',sa.String(24),nullable=False),
          fk('base_publication_id','publications.id',nullable=True),col('manifest',j,nullable=False),
          col('manifest_hash',sa.String(64),nullable=False),actor(),time())
    table('publication_members',fk('publication_id','publications.id',primary_key=True),ident('logical_id',primary_key=True),
          fk('version_id','entity_versions.id',nullable=True),col('ref',j,nullable=False))
    table('active_bindings',ident('channel',primary_key=True),fk('publication_id','publications.id',nullable=True),
          col('revision',sa.Integer,nullable=False),actor('updated_by',True),time('updated_at'),sa.CheckConstraint('revision > 0'))
    table('commands',ident(primary_key=True),actor('actor_id'),col('operation',sa.String(80),nullable=False),
          col('resource_scope',sa.String(100),nullable=False),col('idempotency_key',sa.String(100),nullable=False),
          col('request_hash',sa.String(64),nullable=False),col('receipt',j,nullable=True),
          col('state',sa.String(20),nullable=False),time(),
          sa.UniqueConstraint('actor_id','operation','resource_scope','idempotency_key',name='uq_management_command'),
          sa.CheckConstraint("state IN ('pending','completed')"))
    table('tasks',ident(primary_key=True),fk('command_id','commands.id',nullable=False),col('kind',sa.String(40),nullable=False),
          col('params',j,nullable=False),col('state',sa.String(24),nullable=False),col('revision',sa.Integer,nullable=False),
          col('attempt',sa.Integer,nullable=False),col('fencing',sa.Integer,nullable=False),
          col('lease_token',sa.String(100),nullable=True),time('lease_until',True),col('result',j,nullable=True),actor(),time(),
          sa.CheckConstraint("kind = 'proposal_validation'"),
          sa.CheckConstraint("state IN ('queued','running','succeeded','failed','cancel_requested','cancelled')"))
    table('task_attempts',fk('task_id','tasks.id',primary_key=True),col('attempt',sa.Integer,primary_key=True),
          col('fencing',sa.Integer,nullable=False),time('started_at'),time('finished_at',True),col('outcome',sa.String(24),nullable=True))
    table('schedule_revisions',ident('schedule_id',primary_key=True),col('revision',sa.Integer,primary_key=True),
          col('policy',j,nullable=False),actor(),time())
    table('schedule_bindings',ident('schedule_id',primary_key=True),col('revision',sa.Integer,nullable=False),col('binding_revision',sa.Integer,nullable=False),
          sa.ForeignKeyConstraint(['schedule_id','revision'],['management_schedule_revisions.schedule_id','management_schedule_revisions.revision'],ondelete='RESTRICT'))
    table('audit_events',ident(primary_key=True),fk('command_id','commands.id',nullable=False),actor('actor_id'),
          col('domain',sa.String(24),nullable=False),ident('resource_id',nullable=False),col('action',sa.String(80),nullable=False),
          col('reason',sa.Text,nullable=False),col('before_hash',sa.String(64),nullable=True),col('after_hash',sa.String(64),nullable=True),time())
    table('schema_adoptions',ident(primary_key=True),col('plan_hash',sa.String(64),nullable=False,unique=True),
          col('old_metadata',j,nullable=False),col('before_fingerprint',sa.String(64),nullable=False),
          col('after_fingerprint',sa.String(64),nullable=False),col('data_hash',sa.String(64),nullable=False),
          col('approver',sa.String(100),nullable=False),col('backup_reference',sa.Text,nullable=False),time())
    for name in ('artifacts','proposals','entity_versions','publications'):
        sa.CheckConstraint("domain IN ('recipe','ingredient','price','source_policy')",name='ck_management_'+name+'_domain',table=tables[name])
    sa.Index('ix_management_review_revision',tables['review_decisions'].c.proposal_id,tables['review_decisions'].c.revision,tables['review_decisions'].c.created_at)
    sa.Index('ix_management_task_state',tables['tasks'].c.state,tables['tasks'].c.lease_until)
    return tables

def install_guards(connection):
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql("""CREATE FUNCTION management_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'management immutable record'; END $$""")
        for name in IMMUTABLE:
            connection.exec_driver_sql(f'CREATE TRIGGER management_immutable BEFORE UPDATE OR DELETE ON management_{name} FOR EACH ROW EXECUTE FUNCTION management_reject_mutation()')
        connection.exec_driver_sql("""CREATE FUNCTION management_check_member() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NOT EXISTS (SELECT 1 FROM management_publications p, jsonb_array_elements(p.manifest->'members') m
            WHERE p.id=NEW.publication_id AND m->>'logical_id'=NEW.logical_id AND m->'ref'=NEW.ref
            AND ((NEW.ref->>'kind'='managed' AND NEW.ref->>'version_id'=NEW.version_id AND EXISTS
              (SELECT 1 FROM management_entity_versions v WHERE v.id=NEW.version_id AND v.domain=p.domain
               AND v.logical_id=NEW.logical_id AND v.content_hash=NEW.ref->>'content_hash'))
             OR (NEW.ref->>'kind'='legacy' AND NEW.version_id IS NULL AND NEW.ref->>'logical_id'=NEW.logical_id
               AND p.domain IN ('recipe','ingredient'))))
            THEN RAISE EXCEPTION 'member is not in immutable manifest'; END IF; RETURN NEW; END $$""")
        connection.exec_driver_sql('CREATE TRIGGER management_member_guard BEFORE INSERT ON management_publication_members FOR EACH ROW EXECUTE FUNCTION management_check_member()')
    elif connection.dialect.name == 'sqlite':
        for name in IMMUTABLE:
            for action in ('UPDATE','DELETE'):
                connection.exec_driver_sql(f"CREATE TRIGGER management_{name}_{action.lower()} BEFORE {action} ON management_{name} BEGIN SELECT RAISE(ABORT,'management immutable record'); END")
        connection.exec_driver_sql("""CREATE TRIGGER management_member_guard BEFORE INSERT ON management_publication_members
          WHEN NOT EXISTS (SELECT 1 FROM management_publications p,json_each(p.manifest,'$.members') m
           WHERE p.id=NEW.publication_id AND json_extract(m.value,'$.logical_id')=NEW.logical_id
           AND json(json_extract(m.value,'$.ref'))=json(NEW.ref)
           AND ((json_extract(NEW.ref,'$.kind')='managed' AND json_extract(NEW.ref,'$.version_id')=NEW.version_id AND EXISTS
              (SELECT 1 FROM management_entity_versions v WHERE v.id=NEW.version_id AND v.domain=p.domain
               AND v.logical_id=NEW.logical_id AND v.content_hash=json_extract(NEW.ref,'$.content_hash')))
             OR (json_extract(NEW.ref,'$.kind')='legacy' AND NEW.version_id IS NULL
               AND json_extract(NEW.ref,'$.logical_id')=NEW.logical_id AND p.domain IN ('recipe','ingredient'))))
          BEGIN SELECT RAISE(ABORT,'member is not in immutable manifest'); END""")
