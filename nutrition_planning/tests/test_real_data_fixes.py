"""Regressions found with the real PR17 recipe workbook and All_Food_ID_1018.

Decisions recorded on 2026-11-01:
- every workbook cooking state is classified explicitly; unknown stays unknown;
- an official TFDA average left out of the workbook mean only to avoid double
  counting its child samples is a usable TFDA value;
- recipe grams are the workbook's reviewed weight_g (no unit re-conversion);
- energy/protein use raw composition x recipe grams with factor 1;
- USDA is used, and labelled, only when no TFDA source exists.
"""
from dataclasses import replace
from decimal import Decimal as D
from typing import cast
from collections.abc import Mapping

import pytest
from sqlalchemy import insert, select, update

from nutrition_planning import (ImportedNutritionSelection, load_database_recipe_rows,
                                 load_imported_nutrition, resolve_imported_selections,
                                 validate_database_menu)
from nutrition_planning.imported_nutrition import cooking_state_kind
from nutrition_planning.integration import (RecipeLineProfile, RecipeRows, prepare_database_recipe,
                                            recipe_line_digest)
from nutrition_planning.tests.test_imported_nutrition import IMPORT_ID, imported_menu  # noqa: F401
from nutrition_planning.tests.test_integration import menu  # noqa: F401


@pytest.mark.parametrize('state,kind', [
    ('未熟製原料', 'raw'), ('調理原料／未熟製', 'raw'), ('未熟製果類', 'raw'), ('未熟製蛋原料', 'raw'),
    ('未熟製／半成品', 'raw'), ('未熟製；未熟製原料', 'raw'), ('未熟製原料；未熟製', 'raw'),
    ('依使用者指定熟食；原檔未標生熟', 'cooked'), ('熟製／加工成品；依使用者指定熟食；原檔未標生熟', 'cooked'),
    ('熟製／即食成品；已熟製', 'cooked'), ('加工原料', 'cooked'), ('已熟製', 'cooked'),
])
def test_every_real_workbook_state_is_classified(state, kind):
    assert cooking_state_kind(state) == kind


@pytest.mark.parametrize('state', ['未熟蔬果類', '原檔未標生熟', '未熟製；已熟製', '', None, '等待確認'])
def test_unknown_or_conflicting_states_are_never_guessed(state):
    assert cooking_state_kind(state) is None


def _set_main(db, *, state, method=None, preparation=None, source_name='main', provider='TFDA',
              included=True, exclusion=None, quality_issues=()):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-main').values(
        cooking_state=state, cooking_method=method, preparation=preparation,
        quality_issues=list(quality_issues)))
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-main')).mappings().one()
    raw = {**source['raw_data'], '中文原名': source_name, '來源原文名稱': source_name, '熟製狀態': state,
           '烹飪方式': method, '食用前處理': preparation}
    if exclusion:
        raw['納入平均'] = exclusion
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-main').values(
        raw_data=raw, provider=provider))
    db.execute(update(NutritionSourceLink).where(NutritionSourceLink.source_id == 's-main').values(
        included_in_calculation=included))


def _no_supplements():
    # Fixture Food_IDs (FOOD000001-3) collide with real ones in the repository
    # configuration, so tests pass explicit empty mappings and supplements.
    from nutrition_planning.nutrition_supplement import NutritionSupplements
    return NutritionSupplements({}, {})


def _resolve_and_load(db, profiles, conversion='factor_one'):
    rows = load_database_recipe_rows(db, ['R1'])
    choices, issues = resolve_imported_selections(db, rows, import_id=IMPORT_ID, selections={},
                                                  profiles=profiles, conversion=conversion,
                                                  variant_resolver=lambda request: None)
    if issues:
        return None, issues
    loaded = load_imported_nutrition(db, rows, import_id=IMPORT_ID, selections=choices,
                                     allow_usda=conversion == 'factor_one')
    return loaded, loaded.issues


def test_previously_unrecognised_raw_state_is_selected(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製原料')
    loaded, issues = _resolve_and_load(db, profiles)
    assert not issues and loaded is not None
    assert loaded.line_records['main']['state'] == 'raw'


def test_empty_candidate_list_is_reported_without_asking_the_llm(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='等待確認')
    asked = []
    choices, issues = resolve_imported_selections(
        db, load_database_recipe_rows(db, ['R1']), import_id=IMPORT_ID, selections={}, profiles=profiles,
        variant_resolver=lambda request: asked.append(request))
    assert not asked
    assert [i.code for i in issues] == ['nutrition_state_unknown']


def test_official_average_excluded_only_for_double_counting_is_used(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製', source_name='main平均值', included=False,
              exclusion='否：同組已有子項，排除重複官方平均')
    loaded, issues = _resolve_and_load(db, profiles)
    assert not issues and loaded is not None
    assert loaded.line_records['main']['source']['value_basis'] == 'tfda_official_average_per_100g'


def test_official_average_excluded_for_another_reason_stays_blocked(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製', source_name='main平均值', included=False, exclusion='否：資料品質不足')
    _, issues = _resolve_and_load(db, profiles)
    assert [i.code for i in issues] == ['tfda_official_average_excluded']


def test_compound_observation_state_matches_its_fresh_source(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製', preparation='需烹調')
    db.execute(update(FoodNutrition).where(FoodNutrition.nutrition_id == 'n-main').values(
        cooking_state='未熟製；未熟製原料'))
    loaded, issues = _resolve_and_load(db, profiles)
    assert not issues and loaded is not None
    assert loaded.line_records['main']['source']['cooking_state'] == '未熟製；未熟製原料'
    # The source itself is the fresh 未熟製 sample, not a dried/frozen one.
    raw = db.execute(select(NutritionSource.raw_data).where(NutritionSource.source_id == 's-main')).scalar_one()
    assert raw['熟製狀態'] == '未熟製'


def test_factor_one_rejects_recipe_derived_and_method_specific_cooked_only(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未判定', quality_issues=['recipe_derived_nutrition_unreviewed'])
    _, issues = _resolve_and_load(db, profiles)
    assert [i.code for i in issues] == ['nutrition_recipe_derived_only']
    _set_main(db, state='已熟製', method='水煮')
    _, issues = _resolve_and_load(db, profiles)
    assert [i.code for i in issues] == ['nutrition_cooked_only_needs_raw_mapping']


def test_factor_one_prefers_raw_over_method_specific_cooked(imported_menu):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='已熟製', method='水煮')
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-main-raw', import_id=IMPORT_ID,
        food_id='FOOD000001', source_row=90, standard_name='main', cooking_state='未熟製',
        cooking_method=None, preparation='需烹調', calculation_method='source', energy_kcal=D('500'),
        protein_g=D('20'), nutrition_complete=False, quality_issues=[], raw_data={}))
    db.execute(insert(NutritionSource), dict(source_id='s-main-raw', import_id=IMPORT_ID,
        source_key='TFDA_main_raw', provider='TFDA', source_record_id='raw-main',
        source_url='https://example.invalid/synthetic', source_sheet='原始來源與判定', source_row=90,
        raw_data={'中文原名': 'main', '來源原文名稱': 'main', '熱量(kcal/100g)': '500',
                  '蛋白質(g/100g)': '20', '熟製狀態': '未熟製', '烹飪方式': None, '食用前處理': '需烹調'}))
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-main-raw', source_id='s-main-raw',
                                                  included_in_calculation=True))
    loaded, issues = _resolve_and_load(db, profiles)
    assert not issues and loaded is not None
    assert loaded.line_records['main']['source']['source_id'] == 'raw-main'


def test_usda_is_a_labelled_fallback_only_when_tfda_is_absent(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='加工原料', provider='USDA', source_name='細砂糖')
    loaded, issues = _resolve_and_load(db, profiles)
    assert not issues and loaded is not None
    source = loaded.line_records['main']['source']
    assert source['provider'] == 'USDA' and source['value_basis'] == 'usda_fallback_per_100g'
    # The original TFDA-only mode keeps rejecting USDA.
    _, issues = _resolve_and_load(db, profiles, conversion='t04')
    assert [i.code for i in issues] == ['tfda_source_missing_or_ambiguous']


def test_factor_one_preview_marks_raw_lines_and_reports_status(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    _set_main(db, state='未熟製')
    result = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=_no_supplements())
    payload = result.to_dict()
    nutrition = cast(Mapping, payload['nutrition'])
    assert nutrition['conversion_assumptions'] == ['raw_weight_energy_protein_factor_one']
    assert nutrition['baseline_per_serving'] is not None
    assert payload['status'] in ('nutrition_passed', 'outside_tolerance')
    raw_line = next(item for item in nutrition['sources'] if item['line_id'] == 'main')
    assert raw_line['sample_state'] == 'raw' and raw_line['cooking_evidence'] is None


def test_missing_data_is_reported_as_incomplete_not_as_outside_tolerance(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    _set_main(db, state='未判定', quality_issues=['recipe_derived_nutrition_unreviewed'])
    payload = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=_no_supplements()).to_dict()
    assert payload['status'] == 'calculation_incomplete'
    assert cast(Mapping, payload['nutrition'])['totals'] is None


def _row_set(**line):
    recipe = {'recipe_id': 'R1', 'original_servings': 2}
    base = {'recipe_ingredient_id': 'L1', 'recipe_id': 'R1', 'sequence': 1, 'food_id': 'FOOD000001',
            'canonical_name': 'main', 'weight_g': None, 'unit': '顆', 'is_required': None,
            'adjustment_status': 'approved', 'adjustable_min': None, 'adjustable_max': None}
    base.update(line)
    rows = RecipeRows(('R1',), (recipe,), (base,))
    profile = RecipeLineProfile('FOOD000001', recipe_line_digest(recipe, base), 'raw', True,
                                D('0.000001'), 'test', 'v1', review_status='approved')
    return rows, {'L1': profile}


def test_workbook_grams_are_used_when_the_display_unit_is_not_grams(menu):  # noqa: F811
    policy = replace(menu[5], sample_mappings={'FOOD000001': 'main'})
    rows, profiles = _row_set(source_weight_g=D('55'), weight_status='unverified_conversion')
    prepared = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert not prepared.issues and prepared.recipe is not None
    line = prepared.recipe.ingredients[0]
    assert line.amount_g == D('55') and line.is_required is True


def test_workbook_weight_column_wins_over_the_gram_amount_column(menu):  # noqa: F811
    # Real row RI002108「冬瓜 2大片」: amount 50 g, workbook weight_g 750 (the
    # recipe author's back-calculated nutrition uses 750).  T12 uses 750.
    policy = replace(menu[5], sample_mappings={'FOOD000001': 'main'})
    rows, profiles = _row_set(unit='g', weight_g=D('50'), source_weight_g=D('750'),
                              weight_status='source_explicit')
    prepared = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert not prepared.issues and prepared.recipe is not None
    assert prepared.recipe.ingredients[0].amount_g == D('750')


def test_line_without_any_workbook_grams_still_reports_missing_grams(menu):  # noqa: F811
    policy = replace(menu[5], sample_mappings={'FOOD000001': 'main'})
    rows, profiles = _row_set(source_weight_g=None, weight_status='unresolved')
    prepared = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    assert 'reviewed_gram_conversion_missing' in [i.code for i in prepared.issues]


# --- T12-only nutrition mapping (option 1, 2026-11-01) -----------------------
from nutrition_planning.nutrition_mapping import (NutritionMappingInvalid, SCHEMA_VERSION,  # noqa: E402
                                                  parse_nutrition_mappings)


def _mapping_file(*entries):
    return {'schema_version': SCHEMA_VERSION, 'version': 'test-map-v1', 'mappings': list(entries)}


APPROVED = dict(status='approved', approved_by='使用者', approved_on='2026-11-01', basis='測試指定')


def test_only_approved_mappings_with_an_approver_are_applied():
    parsed = parse_nutrition_mappings(_mapping_file(
        dict(food_id='FOOD000001', nutrition_food_id='FOOD000009', **APPROVED),
        dict(food_id='FOOD000002', nutrition_food_id='FOOD000009', status='pending')))
    assert set(parsed) == {'FOOD000001'}
    assert parsed['FOOD000001'].mapping_version == 'test-map-v1'


@pytest.mark.parametrize('entry', [
    dict(food_id='FOOD000001', nutrition_food_id='FOOD000009', status='approved', approved_on='2026-11-01', basis='x'),
    dict(food_id='FOOD000001', nutrition_food_id='FOOD000001', **APPROVED),
    dict(food_id='FOOD1', nutrition_food_id='FOOD000009', **APPROVED),
    dict(food_id='FOOD000001', nutrition_food_id='FOOD000009', **{**APPROVED, 'approved_on': '昨天'}),
])
def test_incomplete_or_invalid_mapping_fails_closed(entry):
    with pytest.raises(NutritionMappingInvalid):
        parse_nutrition_mappings(_mapping_file(entry))


def test_duplicate_mapping_for_one_food_is_rejected():
    entry = dict(food_id='FOOD000001', nutrition_food_id='FOOD000009', **APPROVED)
    with pytest.raises(NutritionMappingInvalid):
        parse_nutrition_mappings(_mapping_file(entry, entry))


def _add_target_food(db, *, food_id='FOOD000009', name='老薑', record='E1900103'):
    from app.models.nutrition import FoodNutrition, NutritionSource, NutritionSourceLink
    from app.models.recipe import IngredientAlias
    db.execute(insert(IngredientAlias), dict(food_id=food_id, canonical_name=name))
    db.execute(insert(FoodNutrition), dict(nutrition_id='n-target', import_id=IMPORT_ID, food_id=food_id,
        source_row=50, standard_name=name, cooking_state='未熟製', cooking_method=None, preparation='需烹調',
        calculation_method='source', energy_kcal=D('53'), protein_g=D('1.2'), nutrition_complete=False,
        quality_issues=[], raw_data={}))
    db.execute(insert(NutritionSource), dict(source_id='s-target', import_id=IMPORT_ID, source_key='TFDA_' + record,
        provider='TFDA', source_record_id=record, source_url='https://example.invalid/synthetic',
        source_sheet='原始來源與判定', source_row=50,
        raw_data={'中文原名': name, '來源原文名稱': name, '熱量(kcal/100g)': '53', '蛋白質(g/100g)': '1.2',
                  '熟製狀態': '未熟製', '烹飪方式': None, '食用前處理': '需烹調'}))
    db.execute(insert(NutritionSourceLink), dict(nutrition_id='n-target', source_id='s-target',
                                                  included_in_calculation=True))


def test_mapping_changes_only_the_nutrition_source_not_the_recipe_food_id(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    _set_main(db, state='未判定', quality_issues=['recipe_derived_nutrition_unreviewed'])
    _add_target_food(db)
    mappings = parse_nutrition_mappings(_mapping_file(
        dict(food_id='FOOD000001', nutrition_food_id='FOOD000009', **APPROVED)))
    result = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings=mappings)
    payload = result.to_dict()
    assert payload['status'] != 'calculation_incomplete'
    nutrition = cast(Mapping, payload['nutrition'])
    main = next(item for item in nutrition['sources'] if item['line_id'] == 'main')
    assert main['food_id'] == 'FOOD000001'            # recipe Food_ID unchanged
    assert main['source']['food_id'] == 'FOOD000001'
    assert main['source']['source_id'] == 'E1900103'  # nutrition from the mapped TFDA sample
    assert main['source']['nutrition_mapping']['nutrition_food_id'] == 'FOOD000009'
    assert nutrition['nutrition_mappings'] == ['test-map-v1']
    stored = [row['food_id'] for row in load_database_recipe_rows(db, ['R1']).ingredients]
    assert 'FOOD000001' in stored and 'FOOD000009' not in stored


def test_mapping_can_pin_a_tfda_sample_within_the_same_food(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製', source_name='特定品種')   # name differs: identity unverified without mapping
    _, issues = _resolve_and_load(db, profiles)
    assert [i.code for i in issues] == ['tfda_food_identity_unverified']
    mappings = parse_nutrition_mappings(_mapping_file(
        dict(food_id='FOOD000001', source_record_id='new-main', **APPROVED)))
    rows = load_database_recipe_rows(db, ['R1'])
    choices, issues = resolve_imported_selections(db, rows, import_id=IMPORT_ID, selections={}, profiles=profiles,
                                                  conversion='factor_one', mappings=mappings)
    assert not issues
    loaded = load_imported_nutrition(db, rows, import_id=IMPORT_ID, selections=choices, allow_usda=True)
    assert not loaded.issues
    assert loaded.line_records['main']['source']['source_id'] == 'new-main'


def test_mapping_to_a_missing_target_is_reported(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    mappings = parse_nutrition_mappings(_mapping_file(
        dict(food_id='FOOD000001', nutrition_food_id='FOOD000099', **APPROVED)))
    _, issues = resolve_imported_selections(db, load_database_recipe_rows(db, ['R1']), import_id=IMPORT_ID,
        selections={}, profiles=profiles, conversion='factor_one', mappings=mappings)
    assert [i.code for i in issues] == ['nutrition_mapping_target_missing']


def test_invalid_mapping_file_blocks_preview_instead_of_guessing(imported_menu, monkeypatch):
    import nutrition_planning.nutrition_mapping as nm
    db, _, _, _, _, policy, service, participations = imported_menu
    def broken(path=None):
        raise nm.NutritionMappingInvalid('bad')
    monkeypatch.setattr(nm, 'load_nutrition_mappings', broken)
    payload = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one').to_dict()
    assert payload['status'] == 'calculation_incomplete'
    assert [i['code'] for i in payload['input_issues']] == ['nutrition_mapping_invalid']


def test_repository_mapping_file_is_valid_and_has_no_unapproved_effect():
    from nutrition_planning.nutrition_mapping import load_nutrition_mappings
    load_nutrition_mappings()   # must parse; currently no approved entries


def test_mapping_can_pin_an_official_average_left_out_only_for_double_counting(imported_menu):
    db, _, _, _, profiles, _, _, _ = imported_menu
    _set_main(db, state='未熟製', source_name='甘藍平均值', included=False,
              exclusion='否：同組已有子項，排除重複官方平均')
    mappings = parse_nutrition_mappings(_mapping_file(
        dict(food_id='FOOD000001', source_record_id='new-main', **APPROVED)))
    rows = load_database_recipe_rows(db, ['R1'])
    choices, issues = resolve_imported_selections(db, rows, import_id=IMPORT_ID, selections={}, profiles=profiles,
                                                  conversion='factor_one', mappings=mappings)
    assert not issues
    loaded = load_imported_nutrition(db, rows, import_id=IMPORT_ID, selections=choices, allow_usda=True)
    assert not loaded.issues
    assert loaded.line_records['main']['source']['value_basis'] == 'tfda_official_average_per_100g'


# --- T12-only supplements (2026-11-02) ----------------------------------------
from nutrition_planning.nutrition_supplement import (NutritionSupplementInvalid,  # noqa: E402
                                                     SCHEMA_VERSION as SUPP_SCHEMA,
                                                     parse_nutrition_supplements)

SUPP_OK = dict(status='approved', approved_by='使用者', approved_on='2026-11-02', basis='測試')


def _supp(*entries):
    return parse_nutrition_supplements({'schema_version': SUPP_SCHEMA, 'version': 'supp-test-v1',
                                        'entries': list(entries)})


def test_supplement_values_resolve_a_food_without_tfda_and_are_labelled(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    _set_main(db, state='未判定', quality_issues=['recipe_derived_nutrition_unreviewed'])
    supplements = _supp(dict(food_id='FOOD000001', name='水', kind='values', state='raw',
                             values={'energy_kcal': '0', 'protein_g': '0'}, provider='USER_CONFIRMED',
                             source_name='使用者確認', **SUPP_OK))
    payload = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=supplements).to_dict()
    assert payload['status'] != 'calculation_incomplete'
    nutrition = cast(Mapping, payload['nutrition'])
    main = next(item for item in nutrition['sources'] if item['line_id'] == 'main')
    assert main['food_id'] == 'FOOD000001'
    assert main['source']['provider'] == 'USER_CONFIRMED'
    assert main['source']['value_basis'] == 'user_confirmed_per_100g'
    assert nutrition['supplements'] == ['supp-test-v1']


def test_supplement_fill_only_fills_blank_cells(imported_menu):
    from app.models.nutrition import NutritionSource
    db, _, _, _, _, policy, service, participations = imported_menu
    source = db.execute(select(NutritionSource).where(NutritionSource.source_id == 's-oil')).mappings().one()
    db.execute(update(NutritionSource).where(NutritionSource.source_id == 's-oil').values(
        raw_data={**source['raw_data'], '蛋白質(g/100g)': None}))
    blocked = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={},
        nutrition_supplements=_supp()).to_dict()
    assert blocked['status'] == 'calculation_incomplete'
    supplements = _supp(dict(food_id='FOOD000002', name='oil', kind='fill',
                             values={'protein_g': '0', 'energy_kcal': '1'}, provider='USER_CONFIRMED',
                             source_name='使用者確認純油脂蛋白質為 0', **SUPP_OK))
    payload = validate_database_menu(db, ['R1'], staple='不需要主食', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=supplements).to_dict()
    oil = next(item for item in cast(Mapping, payload['nutrition'])['sources'] if item['line_id'] == 'oil')
    assert oil['nutrition_per_100g']['protein_g'] == '0'
    assert oil['nutrition_per_100g']['energy_kcal'] == '900'      # existing value never overwritten
    assert oil['source']['filled_values']['filled'] == ['protein_g']


@pytest.mark.parametrize('entry', [
    dict(food_id='FOOD000001', kind='values', state='raw', values={'energy_kcal': '0'}, provider='USER_CONFIRMED',
         source_name='x', **SUPP_OK),
    dict(food_id='FOOD000001', kind='values', state='raw', values={'energy_kcal': '1', 'protein_g': '1'},
         provider='USDA', source_name='x', **SUPP_OK),
    dict(food_id='FOOD000001', kind='values', state='raw', values={'energy_kcal': 1, 'protein_g': '1'},
         provider='USER_CONFIRMED', source_name='x', **SUPP_OK),
    dict(food_id='FOOD000001', kind='values', state='raw', values={'energy_kcal': '-1', 'protein_g': '1'},
         provider='USER_CONFIRMED', source_name='x', **SUPP_OK),
])
def test_incomplete_supplement_entries_fail_closed(entry):
    with pytest.raises(NutritionSupplementInvalid):
        _supp(entry)


def test_repository_supplement_file_is_valid():
    from nutrition_planning.nutrition_supplement import load_nutrition_supplements
    load_nutrition_supplements()


# --- 2026-11-02: brown rice staple (user decision) ---------------------------

def test_brown_rice_staple_uses_its_reviewed_usda_cooked_value(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    _set_main(db, state='未熟製')
    payload = validate_database_menu(db, ['R1'], staple='糙米飯', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=_no_supplements()).to_dict()
    assert payload['status'] in ('nutrition_passed', 'outside_tolerance')
    staple = cast(Mapping, payload['staple'])
    assert staple['food_id'] == 'FOOD001487' and staple['nutrition_source_id'] == 'USDA_FDC_168875'
    line = next(item for item in cast(Mapping, payload['nutrition'])['sources']
                if item['line_id'] == 'staple:FOOD001487')
    assert line['sample_state'] == 'cooked' and line['source']['provider'] == 'USDA'
    assert line['nutrition_per_100g']['energy_kcal'] == '112'


def test_brown_rice_is_not_offered_outside_the_factor_one_preview(imported_menu):
    db, _, _, _, _, policy, service, participations = imported_menu
    payload = validate_database_menu(db, ['R1'], staple='糙米飯', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='t04').to_dict()
    assert [i['code'] for i in payload['input_issues']] == ['staple_not_supported']


def test_changed_brown_rice_policy_fails_closed(imported_menu, monkeypatch):
    import json
    from pathlib import Path
    from nutrition_planning import integration
    db, _, _, _, _, policy, service, participations = imported_menu
    real = Path(integration.__file__).resolve().parents[1] / 'config/staples.json'
    data = json.loads(real.read_text(encoding='utf-8'))
    data['brown_rice']['grams_per_n'] = '200'
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **k: json.dumps(data, ensure_ascii=False)
                        if self == real else original(self, *a, **k))
    payload = validate_database_menu(db, ['R1'], staple='糙米飯', participations=participations,
        target_service=service, policy=policy, profiles=None, nutrition_import_id=IMPORT_ID,
        conversion='factor_one', nutrition_mappings={}, nutrition_supplements=_no_supplements()).to_dict()
    assert [i['code'] for i in payload['input_issues']] == ['staple_policy_unreviewed_or_changed']
