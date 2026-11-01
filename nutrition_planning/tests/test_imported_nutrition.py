"""T12 reads the versioned tables from feature/data-import, never a fallback JSON."""
from datetime import datetime, timezone
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal as D
from fractions import Fraction as F
from typing import cast
from collections.abc import Mapping

import pytest
from sqlalchemy import Table, insert, select, update

from nutrition_planning import (ImportedNutritionSelection, NutritionDataUnavailable, PlanningInputError,
                                 validate_database_menu, load_database_recipe_rows, load_imported_nutrition,
                                 resolve_imported_selections, audit_food_id_coverage)
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
    result = validate_database_menu(db, ['R1'], staple="不需要主食", participations=participations, target_service=service,
        policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={})
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    assert result.validation.final_estimate.totals == result.targets.target
    source = result.validation.final_estimate.sources[0]['source']
    assert isinstance(source, dict)
    assert source['import_id'] == IMPORT_ID
    assert source['source_id'] == 'new-main'


def test_cooked_food_data_has_priority_over_undetermined_food_id_data(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    db, _, _, _, profiles, _, _, _ = imported_menu
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-main-undetermined', import_id=IMPORT_ID,
        food_id='FOOD000001', source_row=98, standard_name='main', cooking_state='未判定',
        cooking_method=None, preparation=None, calculation_method='source', energy_kcal=D('500'),
        protein_g=D('20'), nutrition_complete=False, quality_issues=[], raw_data={}))
    db.execute(insert(NutritionSource), dict(source_id='s-main-undetermined', import_id=IMPORT_ID,
        source_key='TFDA_main_unknown', provider='TFDA', source_record_id='unknown-main',
        source_url='https://example.invalid/synthetic', source_sheet='原始來源與判定', source_row=98,
        raw_data={'中文原名': 'main', '來源原文名稱': 'main', '熱量(kcal/100g)': '500',
                  '蛋白質(g/100g)': '20', '熟製狀態': '未判定', '烹飪方式': None, '食用前處理': None}))
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main-undetermined',
                                                  source_id='s-main-undetermined', included_in_calculation=True))
    choices, issues = resolve_imported_selections(db, load_database_recipe_rows(db, ['R1']),
        import_id=IMPORT_ID, selections={}, profiles=profiles)
    assert not issues
    assert choices['main'].nutrition_id == 'n-main'


def test_undetermined_food_data_becomes_llm_cooking_baseline(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource
    db, _, _, _, profiles, _, _, _ = imported_menu
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-main').values(cooking_state='未判定'))
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(
        raw_data={**source['raw_data'], '熟製狀態': '未判定'}))
    profiles = {**profiles, 'main': replace(profiles['main'], weight_basis='raw', consumed_as_is=False,
                                            cooking_method='水煮')}
    rows = load_database_recipe_rows(db, ['R1'])
    choices, issues = resolve_imported_selections(db, rows, import_id=IMPORT_ID,
        selections={}, profiles=profiles)
    assert not issues and choices['main'].cooking_state == '未判定'
    imported = load_imported_nutrition(db, rows, import_id=IMPORT_ID, selections=choices)
    assert not imported.issues
    assert imported.line_records['main']['state'] == 'raw'


def test_multiple_food_id_variants_use_versioned_llm_decision(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    from app.models.recipe import RecipeStep
    db, _, _, _, profiles, policy, service, participations = imported_menu
    cast(Table, RecipeStep.__table__).create(db)
    db.execute(insert(RecipeStep), dict(step_id='S1', recipe_id='R1', step_no=1,
                                        instruction_clean='先以小火煎熟主食材，再加入配料。',
                                        cooking_method='煎', method_status='reviewed', review_status='approved'))
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-main-second', import_id=IMPORT_ID,
        food_id='FOOD000001', source_row=99, standard_name='main', cooking_state='已熟製',
        cooking_method=None, preparation=None, calculation_method='source', energy_kcal=D('687.14364'),
        protein_g=D('30.8'), nutrition_complete=False, quality_issues=[], raw_data={}))
    db.execute(insert(NutritionSource), dict(source_id='s-main-second', import_id=IMPORT_ID,
        source_key='TFDA_main_second', provider='TFDA', source_record_id='second-main',
        source_url='https://example.invalid/synthetic', source_sheet='原始來源與判定', source_row=99,
        raw_data={'中文原名': 'main', '來源原文名稱': 'main', '熱量(kcal/100g)': '687.14364',
                  '蛋白質(g/100g)': '30.8', '熟製狀態': '已熟製', '烹飪方式': None, '食用前處理': None}))
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main-second', source_id='s-main-second',
                                                  included_in_calculation=True))
    def resolver(request):
        if request['line']['recipe_ingredient_id'] == 'main':
            assert request['recipe']['recipe_id'] == 'R1'
            assert request['steps'][0]['instruction_clean'] == '先以小火煎熟主食材，再加入配料。'
            return ImportedNutritionSelection('n-main-second', '已熟製', None,
                decision_basis='食譜步驟提及第二樣品的烹調描述', decision_version='llm-method-v1')
        raise AssertionError('only the ambiguous main line should need LLM')
    result = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={}, variant_resolver=resolver)
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    source = next(cast(Mapping, item['source']) for item in result.validation.final_estimate.sources
                  if isinstance(item.get('source'), dict)
                  and cast(Mapping, item['source']).get('food_id') == 'FOOD000001')
    assert isinstance(source, dict)
    assert source['variant_decision']['version'] == 'llm-method-v1'


def test_white_rice_missing_from_import_is_reported_by_food_id(imported_menu):
    db, _, _, _, profiles, policy, service, participations = imported_menu
    result = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={name: ImportedNutritionSelection('n-' + name, '已熟製', None)
                              for name in ('main', 'oil', 'salt')})
    assert result.validation is None
    assert any(issue.code == 'nutrition_food_id_missing' for issue in result.preparation.issues)
    issue = next(issue for issue in result.preparation.issues
                 if issue.code == 'nutrition_food_id_missing')
    assert issue.message == '食材營養資料缺失'
    assert issue.adoption_blocked
    assert result.adoption_blocked
    assert result.input_issues[0]['message'] == '食材營養資料缺失'


def test_t12_backend_payload_exposes_blocking_reason_without_zero_values(imported_menu):
    db, _, _, _, profiles, policy, service, participations = imported_menu
    result = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={})
    payload = result.to_dict()
    assert payload['adoption_blocked'] is True
    issues = cast(tuple[Mapping[str, object], ...], payload['input_issues'])
    assert issues[0]['message'] == '食材營養資料缺失'
    assert '0' not in cast(str, issues[0]['message'])


def test_food_id_audit_reports_coverage_variants_and_unknown_states(imported_menu):
    from app.models.nutrition import FoodNutrition
    db = imported_menu[0]
    report = audit_food_id_coverage(db, import_id=IMPORT_ID)
    assert report.get('recipe_food_id_count') == 3
    assert report.get('missing_food_ids') == []
    assert report.get('ready_for_food_id_resolution') is True
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-main-unknown-state', import_id=IMPORT_ID,
        food_id='FOOD000001', source_row=99, standard_name='main', cooking_state='等待確認',
        cooking_method=None, preparation=None, calculation_method='source', energy_kcal=D('1'),
        protein_g=D('1'), nutrition_complete=False, quality_issues=[], raw_data={}))
    changed = audit_food_id_coverage(db, import_id=IMPORT_ID)
    assert changed.get('multi_observation_food_ids') == ['FOOD000001']
    assert changed.get('unknown_cooking_states') == ['等待確認']
    assert changed.get('ready_for_food_id_resolution') is False


def test_omitted_staple_choice_is_reported_as_missing(imported_menu):
    db, _, _, _, profiles, policy, service, participations = imported_menu
    result = validate_database_menu(db, ['R1'], participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID)
    assert result.validation is None
    assert any(issue.code == 'staple_selection_required' for issue in result.preparation.issues)


def test_changed_staple_policy_is_reported_instead_of_trusted(imported_menu, monkeypatch):
    from pathlib import Path
    original = Path.read_text
    def changed_policy(path, *args, **kwargs):
        content = original(path, *args, **kwargs)
        if path.name == 'staples.json':
            return content.replace('"grams_per_n": "160"', '"grams_per_n": "180"')
        return content
    monkeypatch.setattr(Path, 'read_text', changed_policy)
    db, _, _, _, profiles, policy, service, participations = imported_menu
    result = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID)
    assert result.validation is None
    assert result.preparation.issues[0].code == 'staple_policy_unreviewed_or_changed'


def test_white_rice_legacy_catalog_never_injects_fixed_tfda_sample(imported_menu):
    db, _, _, _, profiles, policy, service, participations = imported_menu
    result = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles,
        nutrition_source='legacy_tfda')
    assert result.validation is None
    assert result.preparation.issues[0].code == 'staple_nutrition_selection_missing'


def test_white_rice_food_296_is_aligned_with_dishes_and_counted(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    db, _, _, _, profiles, policy, service, participations = imported_menu
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(
        raw_data={**source['raw_data'], '熱量(kcal/100g)': '500', '蛋白質(g/100g)': '26.5'}))
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-rice', import_id=IMPORT_ID,
        food_id='FOOD000296', source_row=50, standard_name='白飯', cooking_state='已熟製',
        cooking_method='電鍋煮', preparation=None, calculation_method='source',
        energy_kcal=D('183'), protein_g=D('3.1'), nutrition_complete=False, quality_issues=[], raw_data={}))
    db.execute(insert(NutritionSource), dict(source_id='s-rice', import_id=IMPORT_ID,
        source_key='TFDA_rice', provider='TFDA', source_record_id='RICE-ALT-SOURCE',
        source_url='https://example.invalid/tfda', source_sheet='原始來源與判定', source_row=50,
        raw_data={'中文原名': '白飯', '來源原文名稱': '白飯', '熱量(kcal/100g)': '183',
                  '蛋白質(g/100g)': '3.1', '熟製狀態': '已熟製', '烹飪方式': '電鍋煮', '食用前處理': None}))
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-rice', source_id='s-rice',
                                                  included_in_calculation=True))
    selections = {name: ImportedNutritionSelection('n-' + name, '已熟製', None)
                  for name in ('main', 'oil', 'salt')}
    selections['staple:FOOD000296'] = ImportedNutritionSelection('n-rice', '已熟製', '電鍋煮')
    result = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections=selections)
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_recipe is not None
    assert result.validation.final_recipe.recipe_ids == ('R1',)
    rice = next(line for line in result.validation.final_recipe.ingredients if line.food_id == 'FOOD000296')
    assert F(100) * result.targets.total_n <= F(rice.amount_g) <= F(250) * result.targets.total_n
    assert F(rice.amount_g) < F(160) * result.targets.total_n
    assert rice.sample_id == 'RICE-ALT-SOURCE'
    assert result.staple_snapshot is not None
    assert result.staple_snapshot['grams_per_n'] == '160'
    assert result.staple_snapshot['source_cooking_method'] == '電鍋煮'
    assert result.staple_snapshot['version'] == 'staples-2026-11-01-v2'
    assert result.staple_snapshot['final_grams'] == str(rice.amount_g)
    wrong_method = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={**selections, 'staple:FOOD000296':
                              ImportedNutritionSelection('n-rice', '已熟製', None)})
    assert wrong_method.validation is None
    assert any(issue.code == 'nutrition_variant_mismatch' for issue in wrong_method.preparation.issues)
    rice_source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-rice')).mappings().one()
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-rice').values(cooking_method='蒸'))
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-rice').values(
        raw_data={**rice_source['raw_data'], '烹飪方式': '蒸'}))
    changed_source_method = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections={**selections, 'staple:FOOD000296':
                              ImportedNutritionSelection('n-rice', '已熟製', '蒸')})
    assert changed_source_method.validation is not None
    assert changed_source_method.validation.nutrition_passed
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-rice').values(cooking_method='電鍋煮'))
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-rice').values(
        raw_data={**rice_source['raw_data'], '蛋白質(g/100g)': None}))
    missing = validate_database_menu(db, ['R1'], staple='白飯', participations=participations,
        target_service=service, policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID,
        nutrition_selections=selections)
    assert missing.validation is not None and not missing.validation.nutrition_passed
    assert missing.validation.final_estimate is None
    assert any('staple:FOOD000296:protein_g' in item
               for item in missing.validation.missing)


def run_menu(menu, *, selections=None, import_id: str | None = IMPORT_ID, profiles=None, policy=None):
    db, _, _, _, original_profiles, original_policy, service, participations = menu
    return validate_database_menu(db, ['R1'], staple="不需要主食", participations=participations, target_service=service,
        policy=policy or original_policy, profiles=profiles or original_profiles, nutrition_import_id=import_id,
        nutrition_selections=selections if selections is not None else {
            name: ImportedNutritionSelection('n-' + name, '已熟製', None) for name in ('main', 'oil', 'salt')})


def test_import_id_is_required_and_no_implicit_latest_or_legacy_fallback(imported_menu):
    for import_id, expected in ((None, 'nutrition_import_id_required'), ('unknown', 'nutrition_import_not_found')):
        result = run_menu(imported_menu, import_id=import_id)
        assert result.validation is None
        assert result.preparation.issues[0].code == expected
    result = run_menu(imported_menu, selections={})
    assert result.validation is not None and result.validation.nutrition_passed


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
    result = validate_database_menu(db, ['R1'], staple="不需要主食", participations=imported_menu[7], target_service=imported_menu[6],
        policy=policy, profiles=profiles, nutrition_import_id=IMPORT_ID, nutrition_selections=selections,
        t04_results={'main': receipt})
    assert result.validation is not None and result.validation.nutrition_passed
    assert result.validation.final_estimate is not None
    assert result.validation.final_estimate.totals == result.targets.target
    stale = validate_database_menu(db, ['R1'], staple="不需要主食", participations=imported_menu[7], target_service=imported_menu[6],
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
        old = validate_database_menu(db, ['R1'], staple="不需要主食", participations=imported_menu[7], target_service=imported_menu[6],
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
