import pytest

from app.management.adopt_v3_v1 import (
    BEFORE_SCHEMA, OLD, PROTECTED_TABLES, SOURCE_DB, VERSION, normalize_constraint, validate_manifest,
)


def test_only_equivalent_postgres_check_casts_are_normalized():
    element_cast = "CHECK (((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text])))"
    array_cast = "CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[])))"
    assert normalize_constraint(element_cast) == normalize_constraint(array_cast)
    assert normalize_constraint(array_cast.replace("running", "failed")) != normalize_constraint(element_cast)
    assert normalize_constraint(array_cast.replace("status", "mode")) != normalize_constraint(element_cast)
    unvalidated = array_cast + " NOT VALID"
    assert normalize_constraint(unvalidated) == unvalidated


def test_manifest_is_bound_to_backup_source_shape_and_separate_target():
    backup = "a" * 64
    manifest = dict(version=VERSION, source_database=SOURCE_DB, source_revision=OLD,
                    target_database="meal_planner_1102_v3_management_target_test", approval="change-42",
                    backup_sha256=backup, baseline={"normalized_schema": BEFORE_SCHEMA, "data_sha256": "b" * 64,
                                                    "release": ["release-v3"],
                                                    "rows": dict.fromkeys(PROTECTED_TABLES, 0)})
    validate_manifest(manifest, backup)

    for field, bad in [("target_database", SOURCE_DB), ("source_database", "postgres"),
                       ("source_revision", "d1105merge1"), ("approval", "unreviewed input"),
                       ("baseline", {"normalized_schema": "c" * 64, "data_sha256": "b" * 64})]:
        with pytest.raises(ValueError):
            validate_manifest({**manifest, field: bad}, backup)
    with pytest.raises(ValueError, match="Backup SHA-256"):
        validate_manifest(manifest, "c" * 64)
