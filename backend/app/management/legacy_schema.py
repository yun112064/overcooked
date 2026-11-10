"""PostgreSQL inventory helpers for the legacy management schema transition."""
from datetime import datetime
from pathlib import Path
import sys

from alembic.config import Config
import sqlalchemy as sa

from .contracts import digest


ROOT = Path(__file__).resolve().parents[3]


def schema_snapshot(connection):
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
    return result

def fingerprint(connection):
    return digest('schema', schema_snapshot(connection))


def data_hash(connection, exclude=()):
    parts = {}
    for name in sorted(sa.inspect(connection).get_table_names()):
        if name.startswith('management_') or name == 'alembic_version' or name in exclude:
            continue
        quoted = connection.dialect.identifier_preparer.quote_identifier(name)
        # Keep JSON source text and Unicode bytes intact. Only hash summaries enter the canonical digest.
        result = connection.exec_driver_sql(f"""
            SELECT count(*), encode(sha256(convert_to(coalesce(
                string_agg(row_to_json(t)::text, E'\\n' ORDER BY row_to_json(t)::text COLLATE "C"),
                ''), 'UTF8')), 'hex')
            FROM public.{quoted} t
        """).one()
        parts[name] = tuple(result)
    parts['_sequence_values'] = [tuple(r) for r in connection.exec_driver_sql(
        "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    )]
    return digest('protected-data', parts)

def cfg(connection):
    config=Config(str(ROOT/'alembic.ini'), stdout=sys.stderr)
    config.set_main_option('script_location',str(ROOT/'migrations'))
    config.attributes.update(connection=connection, database_url="postgresql://", configure_logger=False)
    return config
