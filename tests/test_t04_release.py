"""T04-only transactions and version boundaries, with synthetic SQL inputs."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.recipe import RecipeDataRelease, IngredientAlias, SourceArchive, Recipe, RecipeIngredient, RecipeStep, Allergen
from app.models.nutrition import (FoodNutrition, NutritionDataRelease, NutritionFoodMapping,
                                  NutritionImport, NutritionSource, NutritionSourceLink)
from app.services.nutrition_import import NutritionBundle, NUTRIENT_COLUMNS
from app.services.recipe_import import ImportBundle
from app.services.nutrition_release import (NutritionReleaseBundle, install, load_bundle,
                                          recipe_import_matches, T04_TABLES, USAGE)

MODELS = [RecipeDataRelease, SourceArchive, IngredientAlias, Recipe, Allergen, RecipeStep, RecipeIngredient,
          NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink, NutritionFoodMapping, NutritionDataRelease]


@pytest.fixture
def engine():
    url = os.getenv('T04_TEST_DATABASE_URL')
    engine = create_engine(url or 'sqlite://')
    if url:
        from scripts.t04_db_support import guard
        guard(engine, test=True)
        def clear():
            with engine.begin() as c:
                c.execute(text('TRUNCATE ' + ','.join('"'+m.__tablename__+'"' for m in reversed(MODELS)) + ' CASCADE'))
        clear()
    else:
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(engine, tables=[m.__table__ for m in MODELS])
    with Session(engine) as session, session.begin():
        session.add(RecipeDataRelease(release_id='test-recipe', dataset_version='synthetic',
            source_sha256='a'*64, schema_version='synthetic', source_kind='xlsx_import', active=True, row_counts={}))
        session.flush()
        session.add(IngredientAlias(food_id='FOOD000001', canonical_name='新版食材名',
                                     recipe_release_id='test-recipe'))
    yield engine
    if url:
        clear()
    engine.dispose()


@pytest.fixture
def bundle():
    values = {field: None for field in NUTRIENT_COLUMNS.values()}
    values.update(energy_kcal=Decimal('123.4567890123456'), protein_g=None, sugars_g=Decimal('0'))
    files = {role: {'name': role+'.xlsx', 'sha256': 'b'*64} for role in ('recipes','catalog','nutrition')}
    n = NutritionBundle(
        ImportBundle([], [], [], [], [], [], {}), 'test-import', files,
        [{'nutrition_id':'n1', 'import_id':'test-import', 'food_id':'FOOD000001', 'source_row':2,
          'standard_name':'舊來源名稱', 'cooking_state':'未熟製', 'cooking_method':None, 'preparation':None,
          'calculation_method':'source', **values, 'nutrition_complete':False,
          'quality_issues':['missing:protein_g'], 'raw_data':{'原始數值':'123.4567890123456'}}],
        [{'source_id':'s1', 'import_id':'test-import', 'source_key':'TFDA_TEST', 'provider':'TFDA',
          'source_record_id':'TEST', 'source_url':'https://example.invalid/source',
          'source_sheet':'合成測試', 'source_row':2,
          'raw_data':{'熟製狀態':'未熟製', '烹飪方式':None, '蛋白質(g/100g)':None, '糖(g/100g)':0}}],
        [{'nutrition_id':'n1','source_id':'s1','included_in_calculation':True}],
        {'schema_version':'food-nutrition-import-v1', 'source_files':files,'import_id':'test-import',
         'missing_nutrients':{'protein_g':1},'reconciliation':{'source_count_mismatches':[]}})
    return NutritionReleaseBundle({'release_id':'test-nutrition', 'recipe_release_id':'test-recipe',
        'recipe_source_sha256':'a'*64, 'mapping_version':'test-map',
        'mapping_policy':'exact-food-id-candidates-no-approval-v1','usage_status':USAGE}, 'c'*64, n)


def counts(engine):
    with Session(engine) as session:
        return {m.__tablename__:session.scalar(select(func.count()).select_from(m)) for m in MODELS}


def test_only_nutrition_tables_are_written_and_unknowns_are_preserved(engine, bundle):
    before = counts(engine)
    with Session(engine) as session, session.begin():
        result = install(session, bundle)
    with Session(engine) as session:
        row = session.get(FoodNutrition, 'n1')
        assert row.protein_g is None and row.sugars_g == Decimal(0)
        assert row.energy_kcal == Decimal('123.456789012346')
        assert row.raw_data == {'原始數值':'123.4567890123456'}
        assert session.get(IngredientAlias, 'FOOD000001').canonical_name == '新版食材名'
        assert session.get(NutritionDataRelease, 'test-nutrition').usage_status == USAGE
        mappings = session.scalars(select(NutritionFoodMapping)).all()
        assert len(mappings) == 1 and mappings[0].approval_status == 'pending_review'
        assert mappings[0].evidence['name_matches'] is False
        assert mappings[0].evidence['approval_inherited'] is False
    after = counts(engine)
    assert all(after[t] == n for t,n in before.items() if t not in T04_TABLES)
    assert result['approved_mappings'] == 0 and result['cooking_estimates_imported'] == 0


def test_rerun_verifies_all_source_values_without_overwriting(engine, bundle):
    with Session(engine) as session, session.begin():
        install(session, bundle)
    before = counts(engine)
    with Session(engine) as session, session.begin():
        assert install(session, bundle)['status'] == 'unchanged'
    assert counts(engine) == before


@pytest.mark.parametrize('corruption', ['nutrient', 'source', 'link', 'mapping', 'release', 'metadata'])
def test_rerun_rejects_modified_stored_content(engine, bundle, corruption):
    with Session(engine) as session, session.begin():
        install(session, bundle)
    with Session(engine) as session, session.begin():
        if corruption == 'nutrient':
            session.get(FoodNutrition, 'n1').protein_g = Decimal(0)
        elif corruption == 'source':
            session.get(NutritionSource, 's1').raw_data = {'changed':True}
        elif corruption == 'link':
            session.get(NutritionSourceLink, ('n1','s1')).included_in_calculation = False
        elif corruption == 'mapping':
            session.scalar(select(NutritionFoodMapping)).approval_status = 'approved'
        elif corruption == 'release':
            session.get(NutritionDataRelease, 'test-nutrition').recipe_source_sha256 = 'd'*64
        else:
            session.get(NutritionImport, 'test-import').report = {'schema_version':'tampered'}
    with pytest.raises(ValueError, match='differs|differ'):
        with Session(engine) as session, session.begin():
            install(session, bundle)


def test_late_failure_rolls_back_the_entire_nutrition_import(engine, bundle):
    before = counts(engine)
    def fail_on_release(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith('INSERT INTO NUTRITION_DATA_RELEASES'):
            raise RuntimeError('synthetic late failure')
    event.listen(engine, 'before_cursor_execute', fail_on_release)
    try:
        with pytest.raises(RuntimeError, match='synthetic late failure'):
            with Session(engine) as session, session.begin():
                install(session, bundle)
    finally:
        event.remove(engine, 'before_cursor_execute', fail_on_release)
    assert counts(engine) == before


def test_rerun_retains_the_first_validation_timestamp(engine, bundle):
    report = {**bundle.nutrition.report, 'recipe_quality':{'generated_at':'first-validation','counts':{'x':1}}}
    first = replace(bundle,nutrition=replace(bundle.nutrition,report=report))
    with Session(engine) as session, session.begin():
        install(session,first)
    later = replace(bundle,nutrition=replace(bundle.nutrition,report={**report,
        'recipe_quality':{'generated_at':'later-validation','counts':{'x':1}}}))
    with Session(engine) as session, session.begin():
        assert install(session,later)['status'] == 'unchanged'
        assert session.get(NutritionImport,'test-import').report == report


def test_unknown_food_id_does_not_create_or_rename_t01_dictionary(engine, bundle):
    wrong = deepcopy(bundle.nutrition.observations)
    wrong[0]['food_id'] = 'FOOD999999'
    changed = replace(bundle, nutrition=replace(bundle.nutrition, observations=wrong))
    before = counts(engine)
    with pytest.raises(ValueError, match='unknown Food_ID'):
        with Session(engine) as session, session.begin():
            install(session, changed)
    assert counts(engine) == before


def test_mismatched_active_recipe_hash_is_rejected(engine, bundle):
    changed = replace(bundle, manifest={**bundle.manifest, 'recipe_source_sha256':'d'*64})
    with pytest.raises(ValueError, match='Active T01 release'):
        with Session(engine) as session, session.begin():
            install(session, changed)
    assert counts(engine)['nutrition_imports'] == 0


def test_explicit_binding_resolves_historical_evidence_without_approving_mapping(engine, bundle):
    with Session(engine) as session, session.begin():
        install(session, bundle)
    with Session(engine) as session:
        release = session.get(RecipeDataRelease,'test-recipe')
        imported = session.get(NutritionImport,'test-import')
        assert recipe_import_matches(session, release, imported)
        assert recipe_import_matches(session, release, imported, mapping_version='test-map')
        assert not recipe_import_matches(session, release, imported, mapping_version='wrong-map')
        release.source_sha256 = 'd'*64
        assert not recipe_import_matches(session, release, imported)


def test_pending_mapping_still_blocks_formal_calculation(engine, bundle):
    from app.services.meal_calculation import versioned_sql_calculator, IngredientLine
    with Session(engine) as session, session.begin():
        install(session, bundle)
    calculator = versioned_sql_calculator(sessionmaker(bind=engine), recipe_release_id='test-recipe',
        nutrition_import_id='test-import', mapping_version='test-map')
    result = calculator([(IngredientLine(food_id='FOOD000001', name='新版食材名',
        grams_per_n=Decimal('100'), recipe_release_id='test-recipe', weight_basis='raw'), Decimal('100'))])[0]
    assert result.error is not None and 'mapping' in result.error
    assert result.nutrients['energy_kcal'] is None and result.nutrients['protein_g'] is None


def test_manifest_hash_mismatch_is_rejected_before_opening_sources(tmp_path):
    path = tmp_path/'manifest.json'
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='manifest hash mismatch'):
        load_bundle(path, '0'*64)


@pytest.mark.parametrize('failure', ['binding', 'missing-recipe', 'other-release'])
def test_preview_rejects_unbound_or_wrong_release_before_calculating(failure):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from app.services import t12_planning
    configuration = SimpleNamespace(generation_nutrition_import_id='test-import',
        generation_recipe_release_id='test-recipe', generation_food_mapping_version='test-map')
    db = MagicMock()
    db.get.side_effect = [object(),object()]
    db.execute.return_value = ([] if failure == 'missing-recipe' else
        [SimpleNamespace(recipe_id='R1',recipe_release_id='other-release')])
    with (patch.object(t12_planning,'settings',configuration),
          patch.object(t12_planning,'recipe_import_matches',return_value=failure != 'binding'),
          patch.object(t12_planning,'validate_database_menu') as calculator):
        with pytest.raises(t12_planning.T12PlanningUnavailable) as error:
            t12_planning.preview(db,draft=SimpleNamespace(staple='不需要主食'),members={},recipe_ids=['R1'])
    assert error.value.code == 'RECIPE_NUTRITION_RELEASE_MISMATCH'
    calculator.assert_not_called()
    db.connection.assert_not_called()
