"""Portable release identity gates; all source/database fixtures are synthetic."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, text, update
from sqlalchemy.orm import Session

from app.models.nutrition import NutritionFoodMapping
from app.services import reconciled_policy as policy


@pytest.fixture
def portable(tmp_path, monkeypatch):
    for relative in policy.REQUIRED_FILES:
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'{}')
    manifest = dict(status='approved_for_release', calculation_policy='factor_one',
        nutrition_policy_version='synthetic-portable-v1', expected_schema_head='m1111simorder1',
        recipe_release_id='r-test', recipe_source_sha256='a'*64,
        nutrition_import_id='n-test', existing_food_mapping_version='m-test',
        nutrition_import_source_files={'nutrition': {'name': 'source.xlsx', 'sha256': 'b'*64}},
        nutrition_mapping_rows_sha256='c'*64,
        recipe_bindings={'R1': {'original_servings': 2, 'servings_basis': 'portions'}},
        recipe_line_bindings={'RI1': {'recipe_id': 'R1', 'food_id': 'FOOD1', 'weight_g': 100}},
        overlap_review_decision={'blocked_food_ids': [], 'individually_accepted_food_ids': ['FOOD1']},
        approved_overlap_sources=[{'food_id': 'FOOD1', 'nutrition_id': 'n1', 'nutrition_source_id': 's1'}],
        source_files={r: hashlib.sha256(b'{}').hexdigest() for r in policy.REQUIRED_FILES})
    settings = SimpleNamespace(nutrition_policy_manifest=tmp_path/'manifest.json',
        nutrition_policy_manifest_sha256=None, generation_recipe_release_id='r-test',
        generation_nutrition_import_id='n-test', generation_food_mapping_version='m-test')
    monkeypatch.setattr(policy, 'settings', settings)
    monkeypatch.setattr(policy, 'PROJECT_ROOT', tmp_path)
    def write():
        raw = json.dumps(manifest).encode()
        settings.nutrition_policy_manifest.write_bytes(raw)
        settings.nutrition_policy_manifest_sha256 = hashlib.sha256(raw).hexdigest()
    write()
    return manifest, settings, write


def test_portable_requires_explicit_identity_and_opt_in(portable):
    manifest, settings, write = portable
    assert policy.load_policy()['status'] == 'approved_for_release'
    settings.nutrition_policy_manifest = None
    assert not policy.enabled()


@pytest.mark.parametrize('field,value', [
    ('expected_schema_head', 'old'), ('nutrition_import_source_files', {}),
    ('nutrition_mapping_rows_sha256', 'bad'), ('recipe_source_sha256', None)])
def test_incomplete_portable_identity_is_denied(portable, field, value):
    manifest, _, write = portable
    manifest[field] = value
    write()
    with pytest.raises(policy.PolicyUnavailable, match='release_identity_missing'):
        policy.load_policy()


def test_manifest_and_runtime_and_exact_review_remain_bound(portable):
    manifest, settings, write = portable
    settings.nutrition_policy_manifest_sha256 = 'wrong'
    with pytest.raises(policy.PolicyUnavailable, match='manifest_hash'):
        policy.load_policy()
    write()
    settings.generation_food_mapping_version = 'other'
    with pytest.raises(policy.PolicyUnavailable, match='runtime_version'):
        policy.load_policy()
    settings.generation_food_mapping_version = 'm-test'
    manifest['overlap_review_decision']['blocked_food_ids'] = ['FOOD1']
    write()
    with pytest.raises(policy.PolicyUnavailable, match='review_incomplete'):
        policy.load_policy()


def test_legacy_isolated_manifest_keeps_database_name_requirement(portable):
    manifest, _, write = portable
    manifest['status'] = 'approved_for_isolated_acceptance'
    manifest['business_database'] = 'meal_planner_1102_v3_1111_update'
    write()
    assert policy.load_policy()['status'] == 'approved_for_isolated_acceptance'
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
                         scalar=lambda query: 'another_database')
    with pytest.raises(policy.PolicyUnavailable, match='database_mismatch'):
        policy.validate_database(db, manifest)
    manifest['business_database'] = 'another_database'
    write()
    with pytest.raises(policy.PolicyUnavailable, match='not_approved'):
        policy.load_policy()


def test_schema_gate_denies_missing_wrong_or_multiple_heads(portable):
    manifest = portable[0]
    with Session(create_engine('sqlite://')) as db:
        with pytest.raises(policy.PolicyUnavailable, match='schema_mismatch'):
            policy.validate_database(db, manifest)
        db.execute(text('CREATE TABLE alembic_version (version_num TEXT)'))
        for heads in [('old',), ('m1111simorder1', 'other')]:
            db.execute(text('DELETE FROM alembic_version'))
            for head in heads:
                db.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': head})
            with pytest.raises(policy.PolicyUnavailable, match='schema_mismatch'):
                policy.validate_database(db, manifest)


def test_database_source_mapping_and_complete_recipe_identity(portable, monkeypatch):
    manifest = portable[0]
    engine = create_engine('sqlite://')
    NutritionFoodMapping.__table__.create(engine)
    with Session(engine) as db:
        db.execute(text('CREATE TABLE alembic_version (version_num TEXT)'))
        db.execute(text("INSERT INTO alembic_version VALUES ('m1111simorder1')"))
        db.execute(text('CREATE TABLE recipes (recipe_id TEXT, recipe_release_id TEXT)'))
        db.execute(text("INSERT INTO recipes VALUES ('R1', 'r-test')"))
        db.execute(insert(NutritionFoodMapping).values(mapping_id='map1', import_id='n-test',
            recipe_release_id='r-test', food_id='FOOD1', nutrition_id='n1', source_id='s1',
            cooking_state='raw', approval_status='rejected', mapping_version='m-test', evidence={'review': 'old'}))
        manifest['nutrition_mapping_rows_sha256'] = policy.mapping_rows_digest(db, manifest)
        imported = SimpleNamespace(source_files=deepcopy(manifest['nutrition_import_source_files']))
        release = SimpleNamespace(active=True, source_sha256=manifest['recipe_source_sha256'])
        monkeypatch.setattr(db, 'get', lambda cls, key: imported if key == 'n-test' else release)
        monkeypatch.setattr(policy, 'recipe_import_matches', lambda *args, **kwargs: True)
        rows = SimpleNamespace(recipes=[{'recipe_id': 'R1', 'recipe_release_id': 'r-test',
            'original_servings': 2, 'servings_basis': 'portions'}],
            ingredients=[{'recipe_ingredient_id': 'RI1', 'recipe_id': 'R1', 'food_id': 'FOOD1', 'weight_g': 100}])
        monkeypatch.setattr(policy, 'load_database_recipe_rows', lambda *args: rows)
        policy.validate_database(db, manifest)
        imported.source_files['nutrition']['sha256'] = 'd'*64
        with pytest.raises(policy.PolicyUnavailable, match='import_sources_changed'):
            policy.validate_database(db, manifest)
        imported.source_files = deepcopy(manifest['nutrition_import_source_files'])
        db.execute(update(NutritionFoodMapping).values(approval_status='approved'))
        with pytest.raises(policy.PolicyUnavailable, match='mapping_rows_changed'):
            policy.validate_database(db, manifest)
        db.execute(update(NutritionFoodMapping).values(approval_status='rejected'))
        rows.ingredients[0]['weight_g'] = 101
        with pytest.raises(policy.PolicyUnavailable, match='recipe_line_changed'):
            policy.validate_database(db, manifest)
        rows.ingredients[0]['weight_g'] = 100
        rows.ingredients.clear()
        with pytest.raises(policy.PolicyUnavailable, match='recipe_bindings_incomplete'):
            policy.validate_database(db, manifest)
