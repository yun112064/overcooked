"""Read-only adapter for feature/data-import's versioned nutrition tables.

FOOD identifies a food, not a sample. Select an observation and a TFDA source
explicitly; never use a mixed TFDA/USDA/recipe aggregate as TFDA composition.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
import unicodedata

from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from .integration import InputIssue, RecipeDataUnavailable, RecipeRows, recipe_line_digest
from .targets import PlanningInputError, exact
from nutrition_model.food_id_mapping import AMBIGUOUS_NAMES
from .nutrition_mapping import NutritionMapping

NUTRIENT_COLUMNS = {
    '熱量(kcal/100g)': 'energy_kcal', '蛋白質(g/100g)': 'protein_g',
    '脂肪(g/100g)': 'fat_g', '碳水化合物(g/100g)': 'carbohydrate_g',
    '糖(g/100g)': 'sugars_g', '鈉(mg/100g)': 'sodium_mg', '膳食纖維(g/100g)': 'dietary_fiber_g',
}
# Every cooking-state value present in the PR17/All_Food_ID_1018 workbook is
# listed explicitly.  'raw' means TFDA composition before the recipe's cooking
# (T04 conversion needed); 'cooked' means the value already describes food as
# eaten, including processed seasonings/drinks that need no cooking conversion.
# 未判定 is the workbook's undetermined baseline and follows the raw path.
# Values not listed here stay unknown: they are never guessed raw or cooked.
STATES = {
    '未熟製': 'raw', '未判定': 'raw',
    '未熟製原料': 'raw', '未熟製果類': 'raw', '未熟製蛋原料': 'raw',
    '調理原料／未熟製': 'raw', '未熟製／半成品': 'raw',
    '已熟製': 'cooked', '熟製／即食成品': 'cooked', '熟製／加工成品': 'cooked',
    '已熟製成品': 'cooked', '熟製成品': 'cooked', '加工／調味／飲品': 'cooked',
    '加工／醃漬／調理品': 'cooked', '加工飲品／沖調品': 'cooked',
    '調味／醃漬加工品': 'cooked', '沖泡加工品': 'cooked', '醃製蛋品': 'cooked',
    '即食穀物加工品': 'cooked',
    # Workbook-designated ready-to-eat foods ("需復熱", reheated only).
    '依使用者指定熟食': 'cooked',
    # Only 砂糖/鹽 in this workbook: seasonings used as weighed, no conversion.
    '加工原料': 'cooked',
}
# Annotations that qualify another state but do not state raw/cooked alone.
STATE_ANNOTATIONS = frozenset({'原檔未標生熟'})
READY_TO_EAT_FOOD_IDS = {'FOOD000281'}
# Recipe ingredients that are themselves cooked food.  白飯 is confirmed by the
# user (config/staples.json); its TFDA sample is cooked rice (電鍋煮).
COOKED_INGREDIENT_FOOD_IDS = frozenset({'FOOD000296'})
# The only 納入平均 exclusion that leaves an official average usable.
DUPLICATE_AVERAGE_EXCLUSION = '否：同組已有子項，排除重複官方平均'


def cooking_state_kind(state) -> str | None:
    """Normalize one workbook cooking state to 'raw', 'cooked' or None.

    Compound values joined by '；' are accepted only when every non-annotation
    part is known and all parts agree.  Anything else returns None so callers
    report ``nutrition_state_unknown`` instead of guessing.
    """
    if not isinstance(state, str):
        return None
    text = unicodedata.normalize('NFKC', state).strip().replace('/', '／').replace(';', '；')
    if not text:
        return None
    parts = [part.strip() for part in text.split('；') if part.strip()]
    kinds = set()
    for part in parts:
        if part in STATE_ANNOTATIONS:
            continue
        kind = STATES.get(part)
        if kind is None:
            return None
        kinds.add(kind)
    return next(iter(kinds)) if len(kinds) == 1 else None


def _state_kind(row):
    if row['food_id'] in READY_TO_EAT_FOOD_IDS:
        return 'cooked'
    return cooking_state_kind(row['cooking_state'])


class NutritionDataUnavailable(RecipeDataUnavailable):
    """Missing schema/DB failure, distinct from missing compatible nutrition."""
    code = 'NUTRITION_DATA_UNAVAILABLE'


@dataclass(frozen=True)
class ImportedNutritionSelection:
    nutrition_id: str
    cooking_state: str
    cooking_method: str | None
    source_id: str | None = None
    preparation: str | None = None
    decision_basis: str | None = None
    decision_version: str | None = None
    # T12-only nutrition mapping (nutrition_mapping.py); the recipe Food_ID is
    # unchanged, only the nutrition observation/source comes from here.
    mapping: NutritionMapping | None = None


@dataclass(frozen=True)
class ImportedNutritionData:
    line_records: Mapping[str, Mapping]
    issues: tuple[InputIssue, ...]


def current_nutrition_import_id(db: Connection) -> str | None:
    """Return the single current nutrition batch when callers do not pin one.

    The newest `imported_at` record is the application's current data by
    policy.  A caller can still provide an ID for replay/testing, but browser
    requests never choose a historical batch.
    """
    try:
        meta = MetaData()
        batches = Table('nutrition_imports', meta, autoload_with=db)
        row = db.execute(select(batches.c.import_id).order_by(
            batches.c.imported_at.desc(), batches.c.import_id.desc()).limit(1)).mappings().first()
    except (SQLAlchemyError, AttributeError):
        raise NutritionDataUnavailable() from None
    return row['import_id'] if row is not None else None


def _recipe_derived(row) -> bool:
    """Observation averaged from the recipe workbook's own nutrition cells.

    The recipe workbook carries no energy column, so these rows can never give
    an energy value; they are not TFDA/USDA composition.
    """
    issues = row.get('quality_issues') or ()
    return 'recipe_derived_nutrition_unreviewed' in issues


def _select_factor_one(candidates: list) -> tuple[list, str | None]:
    """User decision 2026-11-01 (raw grams, factor 1 for energy/protein).

    Recipe grams are pre-cooking weights, so raw composition is preferred.
    Foods eaten as purchased (processed/seasoning/ready states with no specific
    cooking method) are used as-is.  A method-specific cooked record (e.g.
    已熟製/水煮) is used only for ingredients that are themselves cooked food;
    otherwise the food needs an approved mapping to a raw TFDA Food_ID.
    """
    real = [row for row in candidates if not _recipe_derived(row)]
    raw = [row for row in real if _state_kind(row) == 'raw']
    if raw:
        return raw, None
    as_eaten = [row for row in real if _state_kind(row) == 'cooked'
                and _text(row['cooking_method']) in (None, '未註明')]
    if as_eaten:
        return as_eaten, None
    method_cooked = [row for row in real if _state_kind(row) == 'cooked']
    if method_cooked and all(row['food_id'] in COOKED_INGREDIENT_FOOD_IDS for row in method_cooked):
        return method_cooked, None
    if method_cooked:
        return [], 'nutrition_cooked_only_needs_raw_mapping'
    if real:
        return [], 'nutrition_state_unknown'
    return [], 'nutrition_recipe_derived_only'


def resolve_imported_selections(db: Connection, rows: RecipeRows, *, import_id: str | None,
                                selections: Mapping[str, ImportedNutritionSelection] | None,
                                profiles: Mapping | None = None, variant_resolver=None,
                                conversion: str = 't04',
                                mappings: Mapping[str, NutritionMapping] | None = None) -> tuple[Mapping[str, ImportedNutritionSelection], tuple[InputIssue, ...]]:
    """Resolve nutrition by Food_ID and recipe cooking facts before calling an LLM.

    A resolver is called only if more than one compatible variant remains. Its
    returned selection must record the LLM decision basis and version.
    """
    selected = dict(selections or {})
    if import_id is None:
        import_id = current_nutrition_import_id(db)
    if not isinstance(import_id, str) or not import_id.strip():
        return selected, (InputIssue('nutrition_data_current_batch_missing', '', field='import_id'),)
    try:
        meta = MetaData()
        observations = Table('food_nutrition', meta, autoload_with=db)
        mappings = dict(mappings or {})
        ids = {line['food_id'] for line in rows.ingredients}
        ids |= {mappings[fid].nutrition_food_id for fid in ids if fid in mappings}
        available: dict[str, list] = {}
        for row in db.execute(select(observations).where(observations.c.import_id == import_id,
                                                          observations.c.food_id.in_(ids))).mappings():
            available.setdefault(row['food_id'], []).append(row)
        pinned_records = {m.source_record_id for m in mappings.values() if m.source_record_id}
        pinned: dict[str, set[str]] = {}
        if pinned_records:
            sources = Table('nutrition_sources', meta, autoload_with=db)
            links = Table('nutrition_source_links', meta, autoload_with=db)
            for link in db.execute(select(links.c.nutrition_id, sources.c.source_record_id)
                                   .join(sources, sources.c.source_id == links.c.source_id)
                                   .where(sources.c.import_id == import_id, sources.c.provider == 'TFDA',
                                          sources.c.source_record_id.in_(pinned_records))).mappings():
                pinned.setdefault(link['source_record_id'], set()).add(link['nutrition_id'])
    except (SQLAlchemyError, AttributeError):
        raise NutritionDataUnavailable() from None
    issues: list[InputIssue] = []
    recipes = {row['recipe_id']: dict(row) for row in rows.recipes}
    steps_by_recipe: dict[str, list[Mapping]] = {}
    for step in rows.steps:
        steps_by_recipe.setdefault(step['recipe_id'], []).append(dict(step))
    for line in rows.ingredients:
        lid, fid, rid = line['recipe_ingredient_id'], line['food_id'], line['recipe_id']
        if lid in selected:
            continue
        mapping = mappings.get(fid)
        candidates = available.get(mapping.nutrition_food_id if mapping else fid, [])
        if mapping is not None and mapping.source_record_id:
            candidates = [row for row in candidates
                          if row['nutrition_id'] in pinned.get(mapping.source_record_id, set())]
            if len(candidates) != 1:
                issues.append(InputIssue('nutrition_mapping_target_missing', rid, lid, 'nutrition_mapping'))
                continue
            row = candidates[0]
            selected[lid] = ImportedNutritionSelection(row['nutrition_id'], row['cooking_state'],
                row['cooking_method'], preparation=row['preparation'], mapping=mapping)
            continue
        if not candidates:
            issues.append(InputIssue('nutrition_mapping_target_missing' if mapping else 'nutrition_food_id_missing',
                                     rid, lid, 'nutrition_mapping' if mapping else 'food_id'))
            continue
        profile = (profiles or {}).get(lid)
        method = getattr(profile, 'cooking_method', None)
        if conversion == 'factor_one':
            compatible, reason = _select_factor_one(candidates)
            if reason is not None:
                issues.append(InputIssue(reason, rid, lid, 'nutrition'))
                continue
            if len(compatible) == 1:
                row = compatible[0]
                selected[lid] = ImportedNutritionSelection(row['nutrition_id'], row['cooking_state'],
                                                            row['cooking_method'], preparation=row['preparation'],
                                                            mapping=mapping)
                continue
            method = None
        # A cooked observation with a specific method (e.g. 已熟製/水煮) only
        # matches a recipe line whose own method is that method.  When the
        # recipe method is unknown it is not picked automatically: a boiled-egg
        # value must not stand in for a pan-fried egg.  Unspecified methods
        # (NULL/未註明, typical of ready or processed foods) match any recipe.
        def unspecified(value):
            return _text(value) in (None, '未註明')
        if conversion != 'factor_one':
            cooked_all = [row for row in candidates if _state_kind(row) == 'cooked']
            cooked = [row for row in cooked_all
                      if unspecified(row['cooking_method']) or (method is not None and row['cooking_method'] == method)]
            baseline = [row for row in candidates if row['cooking_state'] == '未判定']
            raw = [row for row in candidates if _state_kind(row) == 'raw' and row['cooking_state'] != '未判定']
            compatible = cooked or baseline or raw
            if not compatible and method is None and cooked_all:
                # Only method-specific cooked data exists for this food; the
                # recipe steps decide between them (single one is the food itself).
                compatible = cooked_all
            if not compatible:
                # Never ask the variant LLM to choose from an empty list.
                unknown = [row for row in candidates if _state_kind(row) is None]
                issues.append(InputIssue('nutrition_state_unknown' if unknown
                                         else 'nutrition_cooked_method_unavailable', rid, lid, 'cooking_state'))
                continue
        if len(compatible) == 1:
            row = compatible[0]
            selected[lid] = ImportedNutritionSelection(row['nutrition_id'], row['cooking_state'],
                                                        row['cooking_method'], preparation=row['preparation'],
                                                        mapping=mapping)
            continue
        request = {'recipe': recipes.get(rid), 'line': dict(line),
                   'steps': steps_by_recipe.get(rid, []),
                   'candidates': [dict(row) for row in compatible], 'cooking_method': method}
        if variant_resolver is None:
            try:
                from llm_cooking_estimator import resolve_nutrition_variant  # type: ignore[import-not-found]
                llm = resolve_nutrition_variant(request)
            except ImportError:
                issues.append(InputIssue('llm_service_unavailable', rid, lid, 'cooking_method'))
                continue
            if not llm.get('success'):
                issues.append(InputIssue(str(llm.get('error', 'llm_variant_decision_invalid')), rid, lid,
                                         'cooking_method'))
                continue
            chosen = next(row for row in compatible if row['nutrition_id'] == llm['nutrition_id'])
            decision = ImportedNutritionSelection(chosen['nutrition_id'], chosen['cooking_state'],
                chosen['cooking_method'], preparation=chosen['preparation'],
                decision_basis=llm['basis'], decision_version=llm['version'])
        else:
            decision = variant_resolver(request)
        if not isinstance(decision, ImportedNutritionSelection) or not decision.decision_basis or not decision.decision_version:
            issues.append(InputIssue('llm_variant_decision_invalid', rid, lid, 'cooking_method'))
            continue
        if decision.nutrition_id not in {row['nutrition_id'] for row in compatible}:
            issues.append(InputIssue('llm_variant_decision_mismatch', rid, lid, 'nutrition'))
            continue
        selected[lid] = replace(decision, mapping=mapping) if mapping else decision
    return selected, tuple(issues)


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _name(value):
    text = _text(value)
    return unicodedata.normalize('NFKC', text) if text is not None else None


def _state_matches(source_state, selection_state) -> bool:
    """A source matches its observation's state, or one part of a compound
    observation state such as 未熟製；未熟製原料 (fresh and frozen samples)."""
    source_state, selection_state = _text(source_state), _text(selection_state)
    if source_state is None or selection_state is None:
        return False
    if source_state == selection_state:
        return True
    parts = {part.strip() for part in selection_state.replace(';', '；').split('；') if part.strip()}
    return len(parts) > 1 and source_state in parts


def _same_variant(source, selection: ImportedNutritionSelection):
    raw = source['raw_data']
    return (isinstance(raw, dict) and _state_matches(raw.get('熟製狀態'), selection.cooking_state)
            and _text(raw.get('烹飪方式')) == selection.cooking_method
            and _text(raw.get('食用前處理')) == selection.preparation)


def _source_name(source):
    if not isinstance(source['raw_data'], dict):
        return None
    names = {_name(source['raw_data'].get(key)) for key in ('中文原名', '來源原文名稱')}
    names.discard(None)
    return next(iter(names)) if len(names) == 1 else None


def _choose_source(candidates: list, canonical_name, selection: ImportedNutritionSelection):
    if not candidates:
        return None, 'tfda_source_missing_or_ambiguous'
    matching = [s for s in candidates if _same_variant(s, selection)]
    if not matching:
        return None, 'tfda_source_variant_mismatch'
    canonical = _name(canonical_name)
    if canonical is None or canonical in AMBIGUOUS_NAMES:
        return None, 'tfda_food_identity_unverified'
    named = [(source, _source_name(source)) for source in matching]
    # A unique TFDA official average named exactly "<standard name>平均值" is
    # the food-level TFDA value; it identifies the food even when individual
    # child samples carry variety names (e.g. 雞蛋(白殼)).
    averages = [s for s, name in named if name == canonical + '平均值']
    if len(averages) > 1:
        return None, 'tfda_source_missing_or_ambiguous'
    if not averages and any(name is None for _, name in named):
        return None, 'tfda_food_identity_unverified'
    exact_names = [s for s, name in named if name == canonical]
    eligible = averages if averages else exact_names
    if not eligible:
        return None, 'tfda_food_identity_unverified'
    if len(eligible) != 1:
        return None, 'tfda_source_missing_or_ambiguous'
    chosen = eligible[0]
    if selection.source_id is not None and selection.source_id != chosen['source_id']:
        return None, 'tfda_official_average_required' if averages else 'tfda_source_missing_or_ambiguous'
    return chosen, None


def load_imported_nutrition(db: Connection, rows: RecipeRows, *, import_id: str | None,
                            selections: Mapping[str, ImportedNutritionSelection],
                            allow_usda: bool = False) -> ImportedNutritionData:
    """Select included TFDA source cells from a pinned import, without DB writes.

    ``source_id`` pins the unique verified TFDA match; it cannot bypass name
    ambiguity or the unique official-average rule. An excluded source,
    another import/food/state/method/preparation, or an ambiguous source fails
    closed. Source values, including NULL and real zero, remain separate from
    the workbook's mixed aggregate values.
    """
    if import_id is None:
        import_id = current_nutrition_import_id(db)
    if not isinstance(import_id, str) or not import_id.strip():
        return ImportedNutritionData({}, (InputIssue('nutrition_data_current_batch_missing', '', field='import_id'),))
    if not isinstance(selections, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, ImportedNutritionSelection)
        or not isinstance(value.nutrition_id, str) or not value.nutrition_id.strip()
        or not isinstance(value.cooking_state, str) or not value.cooking_state.strip()
        or any(item is not None and (not isinstance(item, str) or not item.strip())
               for item in (value.cooking_method, value.preparation, value.source_id,
                            value.decision_basis, value.decision_version))
        or (value.mapping is not None and not isinstance(value.mapping, NutritionMapping))
        for key, value in selections.items()
    ):
        raise PlanningInputError('Explicit nutrition selection IDs and conditions are required.')
    if set(selections) - {line['recipe_ingredient_id'] for line in rows.ingredients}:
        raise PlanningInputError('Nutrition selections contain unselected recipe lines.')
    try:
        meta = MetaData()
        imports = Table('nutrition_imports', meta, autoload_with=db)
        observations = Table('food_nutrition', meta, autoload_with=db)
        sources = Table('nutrition_sources', meta, autoload_with=db)
        links = Table('nutrition_source_links', meta, autoload_with=db)
        batch = db.execute(select(imports).where(imports.c.import_id == import_id)).mappings().one_or_none()
        if batch is None:
            return ImportedNutritionData({}, (InputIssue('nutrition_import_not_found', '', field='import_id'),))
        files = batch['source_files']
        if (not isinstance(files, dict) or set(files) != {'recipes', 'catalog', 'nutrition'}
                or not isinstance(batch['report'], dict)
                or batch['report'].get('schema_version') != 'food-nutrition-import-v1'
                or any(not isinstance(files.get(key), dict)
                       or not files[key].get('name')
                       or not re.fullmatch(r'[0-9a-fA-F]{64}', str(files[key].get('sha256', '')))
                       for key in ('recipes', 'catalog', 'nutrition'))):
            return ImportedNutritionData({}, (InputIssue('nutrition_import_provenance_invalid', ''),))
        expected_import_id = hashlib.sha256(json.dumps([
            'food-nutrition-import-v1', {key: files[key]['sha256'] for key in ('recipes', 'catalog', 'nutrition')}
        ], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if (expected_import_id != import_id or batch['report'].get('import_id') != import_id
                or batch['report'].get('source_files') != files):
            return ImportedNutritionData({}, (InputIssue('nutrition_import_provenance_invalid', ''),))
        selected_ids = {selection.nutrition_id for selection in selections.values()}
        observation_by_id = {r['nutrition_id']: r for r in db.execute(select(observations).where(
            observations.c.import_id == import_id, observations.c.nutrition_id.in_(selected_ids))).mappings()}
        links_by_id: dict[str, list] = {}
        for link in db.execute(select(links).where(links.c.nutrition_id.in_(observation_by_id))).mappings():
            links_by_id.setdefault(link['nutrition_id'], []).append(link)
        source_ids = {link['source_id'] for group in links_by_id.values() for link in group}
        source_by_id = {s['source_id']: s for s in db.execute(select(sources).where(
            sources.c.source_id.in_(source_ids))).mappings()}
        result: dict[str, Mapping] = {}
        issues: list[InputIssue] = []
        recipes = {r['recipe_id']: r for r in rows.recipes}
        for line in rows.ingredients:
            rid, lid, fid = line['recipe_id'], line['recipe_ingredient_id'], line['food_id']
            digest = recipe_line_digest(recipes[rid], line) if rid in recipes else None
            def issue(code):
                issues.append(InputIssue(code, rid, lid, 'nutrition', digest))
            selection = selections.get(lid)
            if selection is None:
                issue('nutrition_selection_missing')
                continue
            observation = observation_by_id.get(selection.nutrition_id)
            mapping = selection.mapping if selection.mapping and selection.mapping.food_id == fid else None
            if selection.mapping is not None and mapping is None:
                issue('nutrition_mapping_mismatch')
                continue
            expected_food = mapping.nutrition_food_id if mapping else fid
            if observation is None or observation['food_id'] != expected_food:
                issue('nutrition_record_not_found_for_import_and_food')
                continue
            if (observation['cooking_state'] != selection.cooking_state
                    or observation['cooking_method'] != selection.cooking_method
                    or observation['preparation'] != selection.preparation):
                issue('nutrition_variant_mismatch')
                continue
            if cooking_state_kind(selection.cooking_state) is None and fid not in READY_TO_EAT_FOOD_IDS:
                issue('nutrition_state_unknown')
                continue
            source_links = links_by_id.get(selection.nutrition_id, [])
            all_sources = [source_by_id[link['source_id']] for link in source_links if link['source_id'] in source_by_id]
            if (not source_links or len(all_sources) != len(source_links)
                    or any(s['import_id'] != import_id for s in all_sources)):
                issue('nutrition_source_links_invalid')
                continue
            included_ids = {link['source_id'] for link in source_links if link['included_in_calculation'] is True}
            # A mapped food is identified by the target food's own standard name.
            canonical = _name(observation['standard_name'] if mapping else line.get('canonical_name'))
            known_averages = [s for s in all_sources if s['provider'] == 'TFDA' and _same_variant(s, selection)
                              and canonical is not None and _source_name(s) == canonical + '平均值']
            if len(known_averages) > 1:
                issue('tfda_source_missing_or_ambiguous')
                continue
            usable_ids = set(included_ids)
            if known_averages and known_averages[0]['source_id'] not in included_ids:
                # The workbook leaves an official average out of its own
                # equal-weight mean only to avoid double counting its child
                # samples.  The average itself is a published TFDA value and
                # may be used as the single selected source.  Any other
                # exclusion reason keeps it unusable.
                reason = _text(known_averages[0]['raw_data'].get('納入平均'))
                if reason != DUPLICATE_AVERAGE_EXCLUSION:
                    issue('tfda_official_average_excluded')
                    continue
                usable_ids.add(known_averages[0]['source_id'])
            candidates = [s for s in all_sources if s['source_id'] in usable_ids and s['provider'] == 'TFDA']
            if not candidates and allow_usda:
                # User decision 2026-11-01: without any TFDA source, a unique
                # included USDA source of the same variant is used and labelled.
                usda = [s for s in all_sources if s['source_id'] in included_ids
                        and s['provider'] == 'USDA' and _same_variant(s, selection)]
                if len(usda) == 1:
                    candidates = usda
                elif len(usda) > 1:
                    issue('usda_source_ambiguous')
                    continue
            if not candidates and not (mapping is not None and mapping.source_record_id):
                # Keep the established code; the field records which
                # non-TFDA provider is the only basis so the cause is explicit.
                providers = {s['provider'] for s in all_sources if s['source_id'] in included_ids}
                issues.append(InputIssue('tfda_source_missing_or_ambiguous', rid, lid,
                                         'recipe_dataset_only' if providers == {'recipe_dataset'}
                                         else 'usda_only' if providers == {'USDA'} else 'nutrition', digest))
                continue
            if mapping is not None and mapping.source_record_id:
                # A pinned sample may be an included source or an official
                # average left out only to avoid double counting.
                # The approved pin names the sample itself, so only the raw /
                # cooked state must agree (a compound observation such as
                # 未熟製；未熟製原料 holds fresh and dried samples).
                chosen = [s for s in all_sources if s['provider'] == 'TFDA'
                          and s['source_record_id'] == mapping.source_record_id
                          and isinstance(s['raw_data'], dict)
                          and _state_matches(s['raw_data'].get('熟製狀態'), selection.cooking_state)
                          and (s['source_id'] in included_ids
                               or _text(s['raw_data'].get('納入平均')) == DUPLICATE_AVERAGE_EXCLUSION)]
                source, source_error = (chosen[0], None) if len(chosen) == 1 else (None, 'nutrition_mapping_target_missing')
            elif candidates and candidates[0]['provider'] == 'USDA':
                source, source_error = candidates[0], None
            else:
                source, source_error = _choose_source(
                    candidates, observation['standard_name'] if mapping else line.get('canonical_name'), selection)
                if source_error == 'tfda_food_identity_unverified' and mapping is not None:
                    # The approved mapping already names this Food_ID; when it
                    # has a single TFDA sample of the selected variant (e.g.
                    # 寧波年糕 → 冷藏寧波年糕), that sample is the food.
                    only = [s for s in candidates if _same_variant(s, selection)]
                    if len(only) == 1:
                        source, source_error = only[0], None
            if source_error is not None:
                issue(source_error)
                continue
            assert source is not None
            raw = source['raw_data']
            pinned_sample = mapping is not None and bool(mapping.source_record_id)
            if (not isinstance(raw, dict) or not _text(source['source_record_id'])
                    or not _state_matches(raw.get('熟製狀態'), selection.cooking_state)
                    or (not pinned_sample and (_text(raw.get('烹飪方式')) != selection.cooking_method
                                               or _text(raw.get('食用前處理')) != selection.preparation))):
                issue('tfda_source_variant_mismatch')
                continue
            values = {}
            try:
                for column, field in NUTRIENT_COLUMNS.items():
                    value = raw.get(column)
                    # JSON source cells can decode as floats; retain their
                    # reported decimal digits, with no float arithmetic.
                    if isinstance(value, float):
                        if not math.isfinite(value):
                            raise PlanningInputError('Invalid TFDA cell')
                        value = str(value)
                    values[field] = None if value is None or value == '' else exact(value)
                    if values[field] is not None and values[field] < 0:
                        raise PlanningInputError('Negative TFDA cell')
            except PlanningInputError:
                issue('invalid_tfda_source_nutrient')
                continue
            # Some product-ready foods are explicitly approved as consumed
            # cooked even when the workbook's broad state says 未熟製.
            # Keep the resolver and calculation state identical.
            state = _state_kind(observation)
            if state is None:
                issue('nutrition_state_unknown')
                continue
            result[lid] = {
                'ingredient_id': source['source_record_id'], 'state': state,
                'standard_name': observation['standard_name'], 'nutrition_per_100g': values,
                'source': {'provider': source['provider'], 'source_id': source['source_record_id'],
                    'source_version': import_id, 'source_url': source['source_url'],
                    'food_id': fid, 'import_id': import_id, 'nutrition_id': selection.nutrition_id,
                    'nutrition_source_id': source['source_id'], 'source_key': source['source_key'],
                    'source_sheet': source['source_sheet'], 'source_row': source['source_row'],
                    'observation_source_row': observation['source_row'],
                    'cooking_state': selection.cooking_state, 'cooking_method': selection.cooking_method,
                    'preparation': selection.preparation, 'source_files': deepcopy(files),
                    'nutrition_mapping': mapping.provenance if mapping else None,
                    'variant_decision': ({'basis': selection.decision_basis,
                                          'version': selection.decision_version,
                                          'is_estimated': True}
                                         if selection.decision_basis else None),
                    'raw_source': deepcopy(raw), 'observation_values': {
                        field: observation[field] for field in NUTRIENT_COLUMNS.values()},
                    'observation_quality_issues': deepcopy(observation['quality_issues']),
                    'source_links': [dict(link) for link in source_links],
                    'value_basis': ('usda_fallback_per_100g' if source['provider'] == 'USDA'
                                    else 'tfda_official_average_per_100g'
                                    if source['source_id'] not in included_ids
                                    else 'included_tfda_source_per_100g')},
            }
    except (SQLAlchemyError, AttributeError):
        raise NutritionDataUnavailable() from None
    return ImportedNutritionData(result, tuple(issues))
