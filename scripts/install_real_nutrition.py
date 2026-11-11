"""Install a hash-verified real nutrition release into a NEW PostgreSQL database."""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, time, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
SCHEMA_HEAD = 'm1111simorder1'
CORE_TABLES = frozenset({
    'recipe_data_releases', 'source_archive', 'ingredient_aliases', 'allergens',
    'recipes', 'recipe_steps', 'recipe_ingredients', 'recipe_covers',
    'nutrition_imports', 'nutrition_sources', 'food_nutrition',
    'nutrition_source_links', 'nutrition_food_mappings', 'nutrition_data_releases',
})
ALLOWED_TABLES = CORE_TABLES | {
    'nutrition_product_sources', 'nutrition_source_candidates', 't04_cooking_receipts',
}


class InstallError(ValueError):
    pass


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def database_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', value):
        raise InstallError('Database name must be a 1-63 character ASCII SQL identifier.')
    return value


def load_bundle(folder):
    folder = folder.resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    if (manifest.get('schema_version') != 'portable-nutrition-v1'
            or manifest.get('schema_head') != SCHEMA_HEAD
            or manifest.get('synthetic') is not False
            or manifest.get('data_file') != 'data.json.gz'
            or manifest.get('policy_file') != 'policy.json'):
        raise InstallError('Unsupported nutrition bundle format or schema head.')
    for name, hash_key in (('data.json.gz', 'data_sha256'), ('policy.json', 'policy_sha256')):
        if digest(folder / name) != manifest.get(hash_key):
            raise InstallError('Bundle hash mismatch: ' + name)
    counts = manifest.get('tables')
    if (not isinstance(counts, dict) or not CORE_TABLES <= counts.keys()
            or not counts.keys() <= ALLOWED_TABLES
            or any(type(count) is not int or count < 0 for count in counts.values())):
        raise InstallError('Bundle table whitelist/counts invalid; identity, order, job and price tables are prohibited.')
    with gzip.open(folder / 'data.json.gz', 'rt', encoding='utf-8') as source:
        payload = json.load(source)
    tables = payload.get('tables') if isinstance(payload, dict) else None
    if not isinstance(tables, dict) or set(tables) != set(counts):
        raise InstallError('Payload tables differ from manifest.')
    for name, rows in tables.items():
        if not isinstance(rows, list) or len(rows) != counts[name] or any(not isinstance(r, dict) for r in rows):
            raise InstallError('Payload row count/shape mismatch: ' + name)
    policy = json.loads((folder / 'policy.json').read_text(encoding='utf-8-sig'))
    if policy.get('status') != 'approved_for_release' or policy.get('expected_schema_head') != SCHEMA_HEAD:
        raise InstallError('A portable approved release policy is required.')
    return manifest, tables, policy


def check_output(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / '.test-runtime').resolve()):
        raise InstallError('Output env must be inside this checkout\'s ignored .test-runtime directory.')
    if path.exists():
        raise InstallError('Output env already exists; refusing to overwrite it.')
    if path.suffix != '.env':
        raise InstallError('Output environment filename must end in .env.')
    return path


def convert_value(column_type, value):
    """Convert exported scalars only; never stringify nested JSON source evidence."""
    from sqlalchemy import Date, DateTime, Time, Numeric, JSON, ARRAY, Uuid, LargeBinary
    if value is None or isinstance(column_type, JSON):
        return value
    if isinstance(column_type, LargeBinary):
        if isinstance(value, dict) and set(value) == {'__bytes_base64__'}:
            return base64.b64decode(value['__bytes_base64__'], validate=True)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise InstallError('Invalid binary source encoding.')
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if isinstance(column_type, Date):
        return date.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Time):
        return time.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Numeric):
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise InstallError('Non-finite numeric source value.')
        return parsed
    if isinstance(column_type, Uuid):
        return UUID(value) if isinstance(value, str) else value
    if isinstance(column_type, ARRAY):
        return [convert_value(column_type.item_type, entry) for entry in value]
    return value


def type_signature(value):
    from sqlalchemy import JSON, Numeric, String, DateTime, Date, Time, Boolean, Integer, LargeBinary
    if isinstance(value, JSON):
        return ('json',)
    if isinstance(value, LargeBinary):
        return ('binary',)
    if isinstance(value, Numeric):
        return ('numeric', value.precision, value.scale)
    if isinstance(value, String):
        return ('text', value.length)
    if isinstance(value, DateTime):
        return ('datetime', value.timezone)
    for kind in (Date, Time, Boolean, Integer):
        if isinstance(value, kind):
            return (kind.__name__,)
    return (str(value).lower(),)


def canonical_rows_digest(table, rows):
    """Typed content hash shared with independent read-only bundle verification."""
    def encode(value):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise InstallError('Non-finite numeric source value.')
            return {'decimal': '0' if value == 0 else format(value, 'f').rstrip('0').rstrip('.')
                    if '.' in format(value, 'f') else format(value, 'f')}
        if isinstance(value, datetime):
            return {'datetime': (value.astimezone(timezone.utc) if value.tzinfo else value).isoformat()}
        if isinstance(value, (date, time)):
            return {type(value).__name__: value.isoformat()}
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {'__bytes_base64__': base64.b64encode(value).decode('ascii')}
        if isinstance(value, UUID):
            return str(value)
        raise TypeError(type(value).__name__)
    normalized = []
    for row in rows:
        typed = {key: convert_value(table.c[key].type, value) for key, value in dict(row).items()}
        normalized.append(json.dumps(typed, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':'), default=encode))
    return hashlib.sha256(json.dumps(sorted(normalized), separators=(',', ':')).encode('utf-8')).hexdigest()


def application_role_name(database, configured=None):
    if configured:
        return database_name(configured)
    candidate = database + '_app'
    if len(candidate) > 63:
        candidate = database[:50] + '_app_' + hashlib.sha256(database.encode()).hexdigest()[:8]
    return database_name(candidate)


def create_new_database(connection, name, app_role):
    from sqlalchemy import text
    database_name(name)
    database_name(app_role)
    if connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'), {'name': name}):
        raise InstallError('Database already exists; no existing database can be initialized by this command.')
    if connection.scalar(text('SELECT 1 FROM pg_roles WHERE rolname=:name'), {'name': app_role}):
        raise InstallError('Application role already exists; choose a new --app-role.')
    connection.exec_driver_sql('CREATE DATABASE "' + name + '"')


def grant_application_access(connection, role, database):
    """Application can read source evidence but cannot rewrite imported sources."""
    quoted = '"' + role + '"'
    connection.exec_driver_sql('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    connection.exec_driver_sql('GRANT CONNECT ON DATABASE "' + database + '" TO ' + quoted)
    connection.exec_driver_sql('GRANT USAGE ON SCHEMA public TO ' + quoted)
    connection.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA public TO ' + quoted)
    connection.exec_driver_sql('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ' + quoted)
    mutable = ('users,user_sessions,households,household_memberships,household_members,'
               'meal_requests,meal_request_revisions,background_jobs,budget_months,usage_reservations,'
               'recognitions,meal_result_decisions,knowledge_crawl_requests,meal_menu_rejections')
    connection.exec_driver_sql('GRANT INSERT, UPDATE, DELETE ON ' + mutable + ' TO ' + quoted)
    connection.exec_driver_sql('GRANT INSERT ON adopted_meal_plans,generation_results,meal_confirmation_events,sim_market_orders TO ' + quoted)
    connection.exec_driver_sql('GRANT UPDATE (meal_request_id,generation_result_id,result_version,request_version,'
        'idempotency_key,request_hash,adopted_by,price_exception_accepted,snapshot) ON adopted_meal_plans TO ' + quoted)
    connection.exec_driver_sql('GRANT INSERT ON management_role_grants,management_artifacts,management_proposals,'
        'management_proposal_revisions,management_validation_runs,management_review_decisions,'
        'management_entity_versions,management_publications,management_publication_members,'
        'management_commands,management_tasks,management_task_attempts,management_audit_events TO ' + quoted)
    connection.exec_driver_sql('GRANT UPDATE ON management_access_state,management_role_grants,management_proposals,'
        'management_commands,management_tasks,management_active_bindings TO ' + quoted)


def validate_schema(actual, expected, dialect):
    """Check columns/types and PK/FK contracts against ORM after real migrations."""
    for name, table in actual.tables.items():
        if name not in ALLOWED_TABLES:
            continue
        model = expected.tables.get(name)
        if model is None or set(table.c.keys()) != set(model.c.keys()):
            raise InstallError('Migration/ORM column mismatch: ' + name)
        for column in table.c:
            expected_column = model.c[column.name]
            expected_type = expected_column.type.dialect_impl(dialect)
            if (type_signature(column.type) != type_signature(expected_type)
                    or column.nullable != expected_column.nullable):
                raise InstallError('Migration/ORM type mismatch: ' + name + '.' + column.name)
        if tuple(c.name for c in table.primary_key) != tuple(c.name for c in model.primary_key):
            raise InstallError('Migration/ORM primary key mismatch: ' + name)
        def foreign_keys(item):
            return {tuple((element.parent.name, element.target_fullname) for element in constraint.elements)
                    for constraint in item.foreign_key_constraints}
        if foreign_keys(table) != foreign_keys(model):
            raise InstallError('Migration/ORM foreign key mismatch: ' + name)


def ordered_rows(table, rows):
    """Handle recipe duplicate-of self references before their dependent rows."""
    references = [fk for fk in table.foreign_keys if fk.column.table.name == table.name]
    if not references:
        return rows
    pending, ordered, available = list(rows), [], {fk.column.name: set() for fk in references}
    while pending:
        ready = [row for row in pending if all(row.get(fk.parent.name) is None
                 or row.get(fk.parent.name) in available[fk.column.name]
                 or row.get(fk.parent.name) == row.get(fk.column.name) for fk in references)]
        if not ready:
            raise InstallError('Missing/cyclic self reference: ' + table.name)
        ordered.extend(ready)
        for row in ready:
            for key in available:
                available[key].add(row.get(key))
        ready_ids = {id(row) for row in ready}
        pending = [row for row in pending if id(row) not in ready_ids]
    return ordered


def install(args):
    from sqlalchemy import MetaData, create_engine, func, insert, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    name = database_name(args.database)
    app_role = application_role_name(name, args.app_role)
    output = check_output(args.output_env)
    folder = args.bundle.resolve()
    manifest, tables, policy = load_bundle(folder)
    admin_raw = os.environ.get('INSTALL_DATABASE_ADMIN_URL')
    if not admin_raw:
        raise InstallError('INSTALL_DATABASE_ADMIN_URL is required; pass it through the process environment.')
    admin_url = make_url(admin_raw)
    if admin_url.get_backend_name() != 'postgresql':
        raise InstallError('Installer requires a PostgreSQL admin URL.')
    target_url = admin_url.set(database=name)
    target_text = target_url.render_as_string(hide_password=False)
    settings_values = {
        'DATABASE_URL': target_text,
        'NUTRITION_POLICY_MANIFEST': (folder / 'policy.json').as_posix(),
        'NUTRITION_POLICY_MANIFEST_SHA256': manifest['policy_sha256'],
        'GENERATION_RECIPE_RELEASE_ID': policy['recipe_release_id'],
        'GENERATION_NUTRITION_IMPORT_ID': policy['nutrition_import_id'],
        'GENERATION_FOOD_MAPPING_VERSION': policy['existing_food_mapping_version'],
        'T12_PREVIEW_CONVERSION': 'factor_one',
    }
    os.environ.update(settings_values)
    # Imports happen after setting the pinned environment; no default runtime DB
    # is accessed. load_policy verifies all source/config hashes before CREATE.
    from app.db.base import Base
    import app.db.models  # noqa: F401
    import app.models  # noqa: F401
    from app.services import reconciled_policy
    policy = reconciled_policy.load_policy()
    config = Config(str(ROOT / 'alembic.ini'))
    config.attributes.update(database_url=target_text, configure_logger=False)
    if ScriptDirectory.from_config(config).get_heads() != [SCHEMA_HEAD]:
        raise InstallError('Code migrations do not have the expected single release head.')
    admin = create_engine(admin_url, isolation_level='AUTOCOMMIT', hide_parameters=True)
    created = False
    try:
        with admin.connect() as connection:
            create_new_database(connection, name, app_role)
            created = True
        command.upgrade(config, SCHEMA_HEAD)
        target = create_engine(target_url, hide_parameters=True)
        try:
            with target.begin() as connection:
                if connection.scalar(text('SELECT current_database()')) != name:
                    raise InstallError('Unexpected new database identity.')
                if set(connection.scalars(text('SELECT version_num FROM alembic_version'))) != {SCHEMA_HEAD}:
                    raise InstallError('New database migration head mismatch.')
                metadata = MetaData()
                metadata.reflect(connection, only=sorted(tables))
                validate_schema(metadata, Base.metadata, target.dialect)
                selected = [table for table in metadata.sorted_tables if table.name in tables]
                # Fresh migrations seed a legacy release. Only this just-created
                # database is cleared, and only allowlisted data tables; runtime
                # identities, jobs and order tables are never imported or copied.
                for table in reversed(selected):
                    connection.execute(table.delete())
                for table in selected:
                    converted = []
                    for row in tables[table.name]:
                        if set(row) != set(table.c.keys()):
                            raise InstallError('Exported columns differ from schema: ' + table.name)
                        converted.append({key: convert_value(table.c[key].type, value) for key, value in row.items()})
                    converted = ordered_rows(table, converted)
                    for offset in range(0, len(converted), 250):
                        connection.execute(insert(table), converted[offset:offset + 250])
                    actual_count = connection.scalar(select(func.count()).select_from(table))
                    if actual_count != manifest['tables'][table.name]:
                        raise InstallError('Installed row count differs: ' + table.name)
                    installed_rows = connection.execute(select(table)).mappings().all()
                    if canonical_rows_digest(table, converted) != canonical_rows_digest(table, installed_rows):
                        raise InstallError('Installed source content differs: ' + table.name)
                with Session(bind=connection) as session:
                    reconciled_policy.validate_database(session, policy)
            with Session(target) as session:
                reconciled_policy.validate_database(session, policy)
            # A new role is created only after source validation succeeds. The
            # generated password is hex and never printed or sent as a CLI arg.
            app_password = secrets.token_hex(32)
            with admin.connect() as connection:
                connection.exec_driver_sql('CREATE ROLE "' + app_role + '" LOGIN NOSUPERUSER NOCREATEDB '
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '" + app_password + "'")
            with target.begin() as connection:
                grant_application_access(connection, app_role, name)
            application_url = target_url.set(username=app_role, password=app_password)
            application = create_engine(application_url, hide_parameters=True)
            try:
                with Session(application) as session:
                    reconciled_policy.validate_database(session, policy)
                    elevated = session.scalar(text('SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls '
                                                   'FROM pg_roles WHERE rolname=current_user'))
                    if elevated:
                        raise InstallError('Application role unexpectedly has administrative privileges.')
                    for table_name in tables:
                        if session.scalar(text("SELECT has_table_privilege(current_user,:table,'INSERT,UPDATE,DELETE')"),
                                          {'table': table_name}):
                            raise InstallError('Application role can modify imported source data.')
            finally:
                application.dispose()
            settings_values['DATABASE_URL'] = application_url.render_as_string(hide_password=False)
        finally:
            target.dispose()
        output.parent.mkdir(parents=True, exist_ok=True)
        # Refuse overwrites even if another process creates the file meanwhile.
        with output.open('x', encoding='utf-8', newline='\n') as destination:
            for key, value in settings_values.items():
                destination.write(key + '=' + json.dumps(value, ensure_ascii=False) + '\n')
        try:
            output.chmod(0o600)
        except OSError:
            pass  # Windows permissions inherit from the user's ignored directory.
        print(json.dumps({'installed': True, 'database': name, 'schema_head': SCHEMA_HEAD,
                          'tables': manifest['tables'], 'policy_sha256': manifest['policy_sha256'],
                          'output_env': str(output), 'synthetic_data_used': False}, ensure_ascii=False))
    except Exception:
        if created:
            print('The new database is retained for diagnosis; it will not be overwritten on retry.', file=sys.stderr)
        raise
    finally:
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'release_data/nutrition-v1')
    parser.add_argument('--database', required=True)
    parser.add_argument('--output-env', type=Path, required=True)
    parser.add_argument('--app-role', help='New restricted login role; defaults to the new database name plus _app.')
    args = parser.parse_args()
    try:
        install(args)
    except InstallError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # DB exception reprs may contain credentials or complete source rows.
        print('Nutrition installation failed (' + type(exc).__name__ + '); no connection URL is printed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
