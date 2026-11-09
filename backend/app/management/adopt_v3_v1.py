"""Apply the reviewed d1105admin2 bridge only to a separate restored V3 database."""

from datetime import datetime, timezone
from importlib import import_module
import re
from uuid import uuid4

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from .contracts import digest
from .legacy_schema import cfg, data_hash, schema_snapshot
from .models import tables
from .schema_v1 import IMMUTABLE


SOURCE_DB = "meal_planner_1102_v3"
TARGET_NAME = re.compile(r"meal_planner_1102_v3_management_target_[a-z0-9_]{1,20}")
APPROVAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
VERSION = "v3-management-adoption-v1"
OLD, BASE, NEW = "d1105admin2", "d1105merge1", "m1107management1"
REPAIR_TABLE = "meal_menu_rejections"
# From the independently migrated baseline plus the legacy Admin table reference.
BEFORE_SCHEMA = "a762afd505fd362d335f969060e422418b8714abea86604b7d654b9fb54d1d81"
AFTER_SCHEMA = "bf9c97661e1a9f95916d2f1461a80a2478f1b38032a0477e315dd93ae7543f55"
SHA256 = re.compile(r"[0-9a-f]{64}")
LITERAL = r"'[A-Za-z0-9_./-]+'"
CAST_FORMS = (
    (re.compile(r"CHECK \(\(\((\w+)\)::text = ANY \(ARRAY\[(.+)\]\)\)\)"),
     re.compile(rf"\(({LITERAL})::character varying\)::text")),
    (re.compile(r"CHECK \(\(\((\w+)\)::text = ANY \(\(ARRAY\[(.+)\]\)::text\[\]\)\)\)"),
     re.compile(rf"({LITERAL})::character varying")),
)
PROTECTED_TABLES = (
    "recipe_steps", "source_archive", "admin_recipe_proposals", "admin_review_events",
    "admin_schedule_runs", "admin_scheduler_heartbeat", "adopted_meal_plans",
)


def normalize_constraint(definition):
    # pg_dump and a fresh Alembic build can render the same array cast differently.
    for outer, inner in CAST_FORMS:
        match = outer.fullmatch(definition)
        if match:
            tokens = [inner.fullmatch(token) for token in match[2].split(", ")]
            if all(tokens):
                return match[1], tuple(token[1] for token in tokens)
    return definition


def normalized_schema(snapshot):
    return {
        name: ({**item, "constraints": [(key, normalize_constraint(value)) for key, value in item["constraints"]]}
               if isinstance(item, dict) and "constraints" in item else item)
        for name, item in snapshot.items()
    }


def validate_target_name(name):
    if not isinstance(name, str) or not TARGET_NAME.fullmatch(name):
        raise ValueError("Expected a new meal_planner_1102_v3_management_target_* database")


def manifest_marker(approval):
    if not isinstance(approval, str) or not APPROVAL_ID.fullmatch(approval):
        raise ValueError("Invalid change approval identifier")
    return "overcooked:v3-management-target:" + approval


def validate_manifest(manifest, backup_sha256):
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise ValueError("Unexpected adoption manifest version")
    validate_target_name(manifest.get("target_database"))
    manifest_marker(manifest.get("approval"))
    if not SHA256.fullmatch(backup_sha256) or manifest.get("backup_sha256") != backup_sha256:
        raise ValueError("Backup SHA-256 does not match the approved manifest")
    if manifest.get("source_database") != SOURCE_DB or manifest.get("source_revision") != OLD:
        raise ValueError("Unexpected source database or revision")
    baseline = manifest.get("baseline")
    if (not isinstance(baseline, dict) or baseline.get("normalized_schema") != BEFORE_SCHEMA
            or not SHA256.fullmatch(str(baseline.get("data_sha256")))):
        raise ValueError("Unrecognized source structure or data fingerprint")
    release, rows = baseline.get("release"), baseline.get("rows")
    if (not isinstance(release, list) or len(release) != 1 or not isinstance(release[0], str)
            or not release[0] or not isinstance(rows, dict) or set(rows) != set(PROTECTED_TABLES)
            or any(type(count) is not int or count < 0 for count in rows.values())):
        raise ValueError("The baseline requires one active release and all protected row counts")


def prepare_connection(c):
    c.exec_driver_sql("SET LOCAL search_path TO public")
    c.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
    c.exec_driver_sql("SET LOCAL lock_timeout = '5s'")


def legacy_tables(c):
    names = set(sa.inspect(c).get_table_names(schema="public"))
    if REPAIR_TABLE in names or any(name.startswith("management_") for name in names):
        raise ValueError("Target already has some of the new schema")
    if c.exec_driver_sql("SELECT version_num FROM alembic_version ORDER BY version_num").scalars().all() != [OLD]:
        raise ValueError("Expected only the d1105admin2 revision")
    return names


def inventory(c, snapshot=None, exclude=()):
    if snapshot is None:
        snapshot = schema_snapshot(c)
    return {
        "normalized_schema": digest("schema", normalized_schema(snapshot)),
        "data_sha256": data_hash(c, exclude=exclude),
        "release": c.exec_driver_sql(
            "SELECT release_id FROM recipe_data_releases WHERE active ORDER BY release_id"
        ).scalars().all(),
        "rows": {name: c.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar_one() for name in PROTECTED_TABLES},
    }


def capture_baseline(c, target, approval, backup_sha256):
    validate_target_name(target)
    manifest_marker(approval)
    prepare_connection(c)
    identity = c.exec_driver_sql("SELECT current_database(), current_setting('transaction_read_only')").one()
    if tuple(identity) != (SOURCE_DB, "on"):
        raise ValueError("Baseline capture requires a read-only transaction on the named V3 source")
    legacy_tables(c)
    manifest = dict(version=VERSION, source_database=SOURCE_DB, source_revision=OLD, target_database=target,
                    approval=approval, backup_sha256=backup_sha256, baseline=inventory(c))
    validate_manifest(manifest, backup_sha256)
    return manifest


def check_target(c, manifest):
    identity = c.execute(sa.text("""
        SELECT datname, shobj_description(oid, 'pg_database'),
               NOT EXISTS (
                   SELECT 1 FROM aclexplode(coalesce(datacl, acldefault('d', datdba)))
                   WHERE grantee = 0 AND privilege_type = 'CONNECT'
               )
        FROM pg_database WHERE datname=current_database()
    """)).one()
    if tuple(identity) != (manifest["target_database"], manifest_marker(manifest["approval"]), True):
        raise ValueError("Target identity, approval marker or private CONNECT policy does not match")


def check_result(c, manifest):
    expected = manifest["baseline"]
    repaired = inventory(c, exclude=(REPAIR_TABLE,))
    if repaired != {**expected, "normalized_schema": AFTER_SCHEMA}:
        raise ValueError("Reconciled schema or protected data differs from the approved baseline")
    if c.exec_driver_sql(f"SELECT count(*) FROM {REPAIR_TABLE}").scalar_one() != 0:
        raise ValueError("Expected an empty meal-menu rejection table")


def verify_management(c, config):
    if c.exec_driver_sql("SELECT version_num FROM alembic_version").scalars().all() != [NEW]:
        raise ValueError("Unexpected final Alembic revision")
    names = {name for name in sa.inspect(c).get_table_names(schema="public") if name.startswith("management_")}
    if names != {table.name for table in tables.values()}:
        raise ValueError("The management table inventory is incomplete")
    guards = c.exec_driver_sql("""
        SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND t.tgname='management_immutable' AND t.tgenabled='O'
    """).scalars().all()
    if set(guards) != {"management_" + name for name in IMMUTABLE}:
        raise ValueError("The management immutability guards are incomplete")
    if c.exec_driver_sql("""
        SELECT (SELECT count(*) FROM management_access_state WHERE id='global' AND revision=1),
               (SELECT count(*) FROM management_role_grants),
               (SELECT count(*) FROM management_proposals),
               (SELECT count(*) FROM management_publications),
               (SELECT count(*) FROM management_active_bindings WHERE publication_id IS NULL),
               (SELECT count(*) FROM management_active_bindings)
    """).one() != (1, 0, 0, 0, 4, 4):
        raise ValueError("New management state is not empty and inactive")
    command.check(config)


def adopt(c, manifest, backup_sha256, *, apply=False):
    """Use a caller-owned transaction. Any exception must roll back the entire transaction."""
    validate_manifest(manifest, backup_sha256)
    if not c.in_transaction():
        raise ValueError("The adoption requires an explicit transaction")
    prepare_connection(c)
    if not apply and c.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise ValueError("Preflight requires a read-only transaction")
    check_target(c, manifest)
    if apply:
        locked = c.exec_driver_sql(
            "SELECT pg_try_advisory_xact_lock(hashtextextended('management:reconciliation',0))"
        ).scalar_one()
        if not locked:
            raise ValueError("Another adoption transaction holds the lock")
        if c.exec_driver_sql("""
            SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                           WHERE datname=current_database() AND pid<>pg_backend_pid())
        """).scalar_one():
            raise ValueError("Another database session is connected to the target")
    names = legacy_tables(c)
    if apply:
        quote = c.dialect.identifier_preparer.quote_identifier
        locked_tables = ", ".join("public." + quote(name) for name in sorted(names))
        c.exec_driver_sql(f"LOCK TABLE {locked_tables} IN SHARE ROW EXCLUSIVE MODE")
    snapshot = schema_snapshot(c)
    before = inventory(c, snapshot)
    if before != manifest["baseline"]:
        raise ValueError("Restored schema or protected data differs from the approved baseline")
    report = dict(mode="apply" if apply else "preflight", source_revision=OLD, target_revision=NEW,
                  backup_sha256=backup_sha256, baseline=before)
    if not apply:
        return report

    repair = import_module("migrations.versions.c4d8e2a6f1b3_adopt_database_menu")
    with Operations.context(MigrationContext.configure(c)):
        repair.upgrade()
    repaired = schema_snapshot(c)
    if {key: value for key, value in repaired.items() if key != REPAIR_TABLE} != snapshot:
        raise ValueError("Repair changed an existing schema object")
    check_result(c, manifest)
    config = cfg(c)
    command.stamp(config, BASE, purge=True)
    command.upgrade(config, NEW)
    check_result(c, manifest)
    verify_management(c, config)
    adoption_id = uuid4().hex
    c.execute(tables["schema_adoptions"].insert().values(
        id=adoption_id, plan_hash=digest(VERSION, manifest),
        old_metadata={"revision": OLD, "version": VERSION, "source_database": SOURCE_DB},
        before_fingerprint=digest("schema", snapshot), after_fingerprint=digest("schema", repaired),
        data_hash=before["data_sha256"], approver=manifest["approval"], backup_reference=backup_sha256,
        created_at=datetime.now(timezone.utc),
    ))
    return {**report, "adoption_id": adoption_id, "management_tables": len(tables)}
