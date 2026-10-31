"""T12 reads the versioned tables from feature/data-import, never a fallback JSON."""
from datetime import datetime, timezone
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal as D
from typing import cast

import pytest
from sqlalchemy import Table, insert, select, update

from nutrition_planning import (ImportedNutritionSelection, NutritionDataUnavailable, PlanningInputError,
                                 validate_database_menu, load_database_recipe_rows, load_imported_nutrition)
from nutrition_planning.tests.test_integration import menu  # shared real SQL fixture

SOURCE_FILES = {key: {'name': key + '.xlsx', 'sha256': str(i) * 64}
                for i, key in enumerate(('recipes', 'catalog', 'nutrition'), 1)}
# Literal produced with feature/data-import's SCHEMA_VERSION/_hash contract.
IMPORT_ID = '6a301cef863a07f347cd43ebb6e8c853ba418c5da1a0bcc75488bd5a019b9c55'


@pytest.fixture
def imported_menu(menu, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'backend'))
    from app.models.nutrition import FoodNutrition, NutritionImport, NutritionSource, NutritionSourceLink
    from app.models.recipe import IngredientAlias

    db, _, _, _, _, _, _, _ = menu
    for model in (IngredientAlias, NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink):
        cast(Table, model.__table__).create(db)
    db.execute(insert(IngredientAlias), [
        dict(food_id=f'FOOD{i:06}', canonical_name=name) for i, name in enumerate(('main', 'oil', 'salt'), 1)])
    db.execute(insert(NutritionImport), dict(import_id=IMPORT_ID, imported_at=datetime.now(timezone.utc),
        source_files=SOURCE_FILES,
        report={'schema_version': 'food-nutrition-import-v1', 'import_id': IMPORT_ID, 'source_files': SOURCE_FILES}))
    for i, (name, energy, protein) in enumerate((('main', '687.14364', '30.8'), ('oil', '900', '0'), ('salt', '0', '0')), 1):
        db.execute(insert(FoodNutrition), dict(nutrition_id='n-' + name, import_id=IMPORT_ID,
            food_id=f'FOOD{i:06}', source_row=i + 1, standard_name=name, cooking_state='已熟製',
            cooking_method=None, preparation=None, calculation_method='source', energy_kcal=D(energy),
            protein_g=D(protein), nutrition_complete=False, quality_issues=['missing:sugars_g'], raw_data={}))
        db.execute(insert(NutritionSource), dict(source_id='s-' + name, import_id=IMPORT_ID,
            source_key='TFDA_' + name, provider='TFDA', source_record_id='new-' + name,
            source_url='https://example.invalid/synthetic', source_sheet='原始來源與判定', source_row=i + 1,
            raw_data={'中文原名': name, '來源原文名稱': name,
                      '熱量(kcal/100g)': energy, '蛋白質(g/100g)': protein,
                      '熟製狀態': '已熟製', '烹飪方式': None, '食用前處理': None}))
        db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-' + name, source_id='s-' + name,
                                                             included_in_calculation=True))
    return menu


def test_default_menu_reads_feature_import_data_without_reading_old_catalog(imported_menu):
    db, _, _, _, profiles, policy, service, participations = imported_menu
    policy.catalog_path.unlink()
    result = validate_database_menu(db, ['R1'], participations=participations, target_service=service,
        policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={name: ImportedNutritionSelection('n-' + name, '已熟製', None)
                              for name in ('main', 'oil', 'salt')})
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    assert result.validation.final_estimate.totals == result.targets.target
    source = result.validation.final_estimate.sources[0]['source']
    assert isinstance(source, dict)
    assert source['import_id'] == IMPORT_ID
    assert source['source_id'] == 'new-main'


def run_menu(menu, *, selections=None, import_id: str | None = IMPORT_ID, profiles=None, policy=None):
    db, _, _, _, original_profiles, original_policy, service, participations = menu
    return validate_database_menu(db, ['R1'], participations=participations, target_service=service,
        policy=policy or original_policy, profiles=profiles or original_profiles, nutrition_import_id=import_id,
        nutrition_selections=selections if selections is not None else {
            name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')})


def test_import_id_is_required_and_no_implicit_latest_or_legacy_fallback(imported_menu):
    for import_id, expected in ((None, 'nutrition_import_id_required'), ('unknown', 'nutrition_import_not_found')):
        result = run_menu(imported_menu, import_id=import_id)
        assert result.validation is None
        assert result.preparation.issues[0].code == expected
    result = run_menu(imported_menu, selections={})
    assert result.validation is None
    assert {issue.code for issue in result.preparation.issues} == {'nutrition_selection_missing'}


def test_missing_schema_is_not_reported_as_missing_nutrition_or_legacy_success(menu):
    with pytest.raises(NutritionDataUnavailable):
        run_menu(menu)


def test_missing_source_nutrient_cannot_be_filled_from_aggregate(imported_menu):
    from app.models.nutrition import NutritionSource
    db = imported_menu[0]
    source = db.execute(NutritionSource.__table__.select().where(NutritionSource.source_id == 's-main')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(
        raw_data={**source['raw_data'], '蛋白質(g/100g)': None}))
    result = run_menu(imported_menu)
    assert result.validation is not None and not result.validation.nutrition_passed
    assert result.final_food_ids == ()
    assert result.validation.reason == 'missing_nutrition_or_conversion'


@pytest.mark.parametrize('field,value,code', [
    ('cooking_state', '未判定', 'nutrition_variant_mismatch'),
    ('cooking_method', '烤', 'nutrition_variant_mismatch'),
    ('preparation', '去皮', 'nutrition_variant_mismatch'),
    ('nutrition_id', 'not-selected', 'nutrition_record_not_found_for_import_and_food'),
])
def test_exact_variant_query_does_not_substitute_other_conditions(imported_menu, field, value, code):
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['main'] = replace(selections['main'], **{field: value})
    result = run_menu(imported_menu, selections=selections)
    assert result.validation is None
    assert result.preparation.issues[0].code == code


@pytest.mark.parametrize('provider,included', [('USDA', True), ('recipe_dataset', True), ('TFDA', False)])
def test_other_providers_and_excluded_sources_cannot_supply_tfda_values(imported_menu, provider, included):
    from app.models.nutrition import NutritionSource, NutritionSourceLink
    db = imported_menu[0]
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(provider=provider))
    db.execute(update(NutritionSourceLink).where(NutritionSourceLink.source_id == 's-main').values(
        included_in_calculation=included))
    result = run_menu(imported_menu)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'tfda_source_missing_or_ambiguous'


def test_aggregate_is_not_used_in_place_of_selected_tfda_source(imported_menu):
    from app.models.nutrition import FoodNutrition
    db = imported_menu[0]
    db.execute(update(FoodNutrition).values(energy_kcal=D('99999'), protein_g=D('99999')))
    result = run_menu(imported_menu)
    assert result.validation is not None and result.validation.final_estimate is not None
    assert result.validation.final_estimate.totals == result.targets.target
    snapshot = result.validation.final_estimate.sources[0]['source']
    assert isinstance(snapshot, dict)
    assert snapshot['value_basis'] == 'included_tfda_source_per_100g'


def test_source_id_cannot_override_ambiguous_tfda_identity(imported_menu):
    from app.models.nutrition import NutritionSource, NutritionSourceLink
    db = imported_menu[0]
    original = dict(db.execute(NutritionSource.__table__.select().where(
        NutritionSource.source_id == 's-main')).mappings().one())
    db.execute(insert(NutritionSource), {**original, 'source_id': 'second', 'source_key': 'second',
                                                   'source_record_id': 'different-TFDA'})
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main', source_id='second',
                                                          included_in_calculation=True))
    result = run_menu(imported_menu)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'tfda_source_missing_or_ambiguous'
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['main'] = replace(selections['main'], source_id='s-main')
    assert run_menu(imported_menu, selections=selections).validation is None


def test_same_food_can_select_different_tfda_variants_for_different_recipe_lines(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource
    db, recipes, lines, _, profiles, policy, service, participations = imported_menu
    db.execute(lines.update().where(lines.c.recipe_ingredient_id == 'oil').values(food_id='FOOD000001', canonical_name='main'))
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-oil').values(
        food_id='FOOD000001', standard_name='main', cooking_state='未熟製'))
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-oil')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-oil').values(raw_data={
        **source['raw_data'], '中文原名': 'main', '來源原文名稱': 'main', '熟製狀態': '未熟製'}))
    rows = load_database_recipe_rows(db, ['R1'])
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['oil'] = ImportedNutritionSelection('n-oil', '未熟製', None)
    actual = load_imported_nutrition(db, rows, import_id=IMPORT_ID, selections=selections)
    assert not actual.issues
    assert actual.line_records['main']['ingredient_id'] == 'new-main'
    assert actual.line_records['oil']['ingredient_id'] == 'new-oil'


def test_invalid_selection_type_is_an_input_error_not_a_database_outage(imported_menu):
    db = imported_menu[0]
    with pytest.raises(PlanningInputError):
        load_imported_nutrition(db, load_database_recipe_rows(db, ['R1']), import_id=IMPORT_ID, selections={'main': {}})  # type: ignore[arg-type]


def test_cooked_sample_from_another_method_is_not_used_for_the_recipe(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource
    from nutrition_planning import CookingEvidence
    db, _, _, _, profiles, policy, _, _ = imported_menu
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-main').values(cooking_method='水煮'))
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(
        raw_data={**source['raw_data'], '烹飪方式': '水煮'}))
    profiles = {**profiles, 'main': replace(profiles['main'], consumed_as_is=False, cooking_method='烤',
                                            evidence_id='roast', cooking_context={'method': '烤'})}
    policy = replace(policy, approved_evidence={'roast': CookingEvidence('roast', 'new-main', '烤',
        source='synthetic', source_version='1', conditions={'method': '烤'})})
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['main'] = replace(selections['main'], cooking_method='水煮')
    result = run_menu(imported_menu, profiles=profiles, policy=policy, selections=selections)
    assert result.validation is None
    assert 'imported_cooking_method_mismatch' in {issue.code for issue in result.preparation.issues}


def test_imported_raw_tfda_and_t04_coefficients_work_without_legacy_catalog(imported_menu, monkeypatch):
    import importlib
    from pathlib import Path
    from app.models.nutrition import FoodNutrition, NutritionSource
    from nutrition_planning import T04CookingResult
    db, _, _, _, profiles, policy, _, _ = imported_menu
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-main').values(cooking_state='未熟製'))
    source = dict(db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one())
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(raw_data={
        **source['raw_data'], '熟製狀態': '未熟製', '熱量(kcal/100g)': '858.92955', '蛋白質(g/100g)': '38.5'}))
    context = {'juices_consumed': False}
    profiles = {**profiles, 'main': replace(profiles['main'], weight_basis='raw', consumed_as_is=False,
                                            cooking_method='烤', cooking_context=context)}
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'nutrition_model'))
    t04 = importlib.import_module('cooked_nutrition_service')
    raw = {'ingredient_id': 'new-main', 'standard_name': 'main', 'state': 'raw',
           'nutrition_per_100g': {'energy_kcal': 858.92955, 'protein_g': 38.5},
           'source': {'provider': 'TFDA', 'source_id': 'new-main', 'source_version': IMPORT_ID}}
    monkeypatch.setattr(t04.yields, 'get_yield_references', lambda *args, **kwargs: [
        {'reference_id': 'synthetic-reference', 'match_type': 'analogy'}])
    def estimator(payload):
        return {'yield_factor': 0.75, 'reference_ids': ['synthetic-reference'],
                'nutrient_retention_factors': {key: 0.8 for key in t04.NUTRIENTS},
                'assumptions': ['Synthetic integration example'], 'missing_information': []}
    calculated = t04.calculate_cooking('main', 200, '烤', [raw], lambda _: raw,
                                      cooking_context=context, estimator=estimator)
    receipt = T04CookingResult(profiles['main'].input_digest, 'FOOD000001', 'raw', '200', '烤',
                               context, 'synthetic-T04-v1', calculated, nutrition_import_id=IMPORT_ID,
                               nutrition_id='n-main', nutrition_source_id='s-main')
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['main'] = ImportedNutritionSelection('n-main', '未熟製', None)
    policy.catalog_path.unlink()
    result = validate_database_menu(db, ['R1'], participations=imported_menu[7], target_service=imported_menu[6],
        policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID, nutrition_selections=selections,
        t04_results={'main': receipt})
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    assert result.validation.final_estimate.totals == result.targets.target
    stale = validate_database_menu(db, ['R1'], participations=imported_menu[7], target_service=imported_menu[6],
        policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID, nutrition_selections=selections,
        t04_results={'main': replace(receipt, nutrition_import_id='different-batch')})
    assert stale.validation is None
    assert stale.preparation.issues[0].code == 't04_import_snapshot_mismatch'
    for wrong in ('source_version', 'raw_value'):
        old_result = deepcopy(calculated)
        if wrong == 'source_version':
            old_result['nutrient_sources']['protein_g']['source']['source_version'] = 'old-version'
        else:
            old_result['raw_reference']['nutrition']['protein_g'] = 9999
        old = validate_database_menu(db, ['R1'], participations=imported_menu[7], target_service=imported_menu[6],
            policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID, nutrition_selections=selections,
            t04_results={'main': replace(receipt, result=old_result)})
        assert old.validation is None


def test_import_report_must_match_its_files_and_derived_import_id(imported_menu):
    from app.models.nutrition import NutritionImport
    db = imported_menu[0]
    db.execute(update(NutritionImport).values(report={'schema_version': 'food-nutrition-import-v1',
                                                    'import_id': 'incorrect', 'source_files': SOURCE_FILES}))
    result = run_menu(imported_menu)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'nutrition_import_provenance_invalid'


def test_link_to_another_foods_tfda_sample_is_not_a_verified_mapping(imported_menu):
    from app.models.nutrition import NutritionSource
    db = imported_menu[0]
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(raw_data={
        **source['raw_data'], '中文原名': 'another-food', '來源原文名稱': 'another-food'}))
    result = run_menu(imported_menu)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'tfda_food_identity_unverified'


def test_unique_official_average_is_used_and_cannot_be_overridden(imported_menu):
    from app.models.nutrition import NutritionSource, NutritionSourceLink
    db = imported_menu[0]
    original = dict(db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one())
    db.execute(insert(NutritionSource), {**original, 'source_id': 'average', 'source_key': 'average',
        'source_record_id': 'TFDA-average', 'raw_data': {**original['raw_data'],
            '中文原名': 'main平均值', '來源原文名稱': 'main平均值'}})
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main', source_id='average', included_in_calculation=True))
    result = run_menu(imported_menu)
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    assert result.validation.final_estimate.sources[0]['sample_id'] == 'TFDA-average'
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')}
    selections['main'] = replace(selections['main'], source_id='s-main')
    rejected = run_menu(imported_menu, selections=selections)
    assert rejected.validation is None
    assert rejected.preparation.issues[0].code == 'tfda_official_average_required'


def test_excluded_official_average_does_not_silently_fall_back_to_another_sample(imported_menu):
    from app.models.nutrition import NutritionSource, NutritionSourceLink
    db = imported_menu[0]
    original = dict(db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one())
    db.execute(insert(NutritionSource), {**original, 'source_id': 'average', 'source_key': 'average',
        'source_record_id': 'TFDA-average', 'raw_data': {**original['raw_data'],
            '中文原名': 'main平均值', '來源原文名稱': 'main平均值'}})
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main', source_id='average', included_in_calculation=False))
    result = run_menu(imported_menu)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'tfda_official_average_excluded'
