"""Read-only adapter for feature/data-import's versioned nutrition tables.

FOOD identifies a food, not a sample. Select an observation and a TFDA source
explicitly; never use a mixed TFDA/USDA/recipe aggregate as TFDA composition.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
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

NUTRIENT_COLUMNS = {
    '熱量(kcal/100g)': 'energy_kcal', '蛋白質(g/100g)': 'protein_g',
    '脂肪(g/100g)': 'fat_g', '碳水化合物(g/100g)': 'carbohydrate_g',
    '糖(g/100g)': 'sugars_g', '鈉(mg/100g)': 'sodium_mg', '膳食纖維(g/100g)': 'dietary_fiber_g',
}
STATES = {'未熟製': 'raw', '已熟製': 'cooked'}


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


@dataclass(frozen=True)
class ImportedNutritionData:
    line_records: Mapping[str, Mapping]
    issues: tuple[InputIssue, ...]


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _name(value):
    text = _text(value)
    return unicodedata.normalize('NFKC', text) if text is not None else None


def _choose_source(candidates: list, canonical_name, selection: ImportedNutritionSelection):
    if not candidates:
        return None, 'tfda_source_missing_or_ambiguous'
    matching = [s for s in candidates if isinstance(s['raw_data'], dict)
                and _text(s['raw_data'].get('熟製狀態')) == selection.cooking_state
                and _text(s['raw_data'].get('烹飪方式')) == selection.cooking_method
                and _text(s['raw_data'].get('食用前處理')) == selection.preparation]
    if not matching:
        return None, 'tfda_source_variant_mismatch'
    canonical = _name(canonical_name)
    if canonical is None or canonical in AMBIGUOUS_NAMES:
        return None, 'tfda_food_identity_unverified'
    named = []
    for source in matching:
        names = {_name(source['raw_data'].get(key)) for key in ('中文原名', '來源原文名稱')}
        names.discard(None)
        if len(names) != 1:
            return None, 'tfda_food_identity_unverified'
        named.append((source, next(iter(names))))
    averages = [s for s, name in named if name == canonical + '平均值']
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
                            selections: Mapping[str, ImportedNutritionSelection]) -> ImportedNutritionData:
    """Select included TFDA source cells from a pinned import, without DB writes.

    ``source_id`` pins the unique verified TFDA match; it cannot bypass name
    ambiguity or the unique official-average rule. An excluded source,
    another import/food/state/method/preparation, or an ambiguous source fails
    closed. Source values, including NULL and real zero, remain separate from
    the workbook's mixed aggregate values.
    """
    if not isinstance(import_id, str) or not import_id.strip():
        return ImportedNutritionData({}, (InputIssue('nutrition_import_id_required', '', field='import_id'),))
    if not isinstance(selections, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, ImportedNutritionSelection)
        or not isinstance(value.nutrition_id, str) or not value.nutrition_id.strip()
        or not isinstance(value.cooking_state, str) or not value.cooking_state.strip()
        or any(item is not None and (not isinstance(item, str) or not item.strip())
               for item in (value.cooking_method, value.preparation, value.source_id))
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
            if observation is None or observation['food_id'] != fid:
                issue('nutrition_record_not_found_for_import_and_food')
                continue
            if (observation['cooking_state'] != selection.cooking_state
                    or observation['cooking_method'] != selection.cooking_method
                    or observation['preparation'] != selection.preparation):
                issue('nutrition_variant_mismatch')
                continue
            if selection.cooking_state not in STATES:
                issue('nutrition_state_unknown')
                continue
            source_links = links_by_id.get(selection.nutrition_id, [])
            all_sources = [source_by_id[link['source_id']] for link in source_links if link['source_id'] in source_by_id]
            if (not source_links or len(all_sources) != len(source_links)
                    or any(s['import_id'] != import_id for s in all_sources)):
                issue('nutrition_source_links_invalid')
                continue
            included_ids = {link['source_id'] for link in source_links if link['included_in_calculation'] is True}
            candidates = [s for s in all_sources if s['source_id'] in included_ids and s['provider'] == 'TFDA']
            source, source_error = _choose_source(candidates, line.get('canonical_name'), selection)
            if source_error is not None:
                issue(source_error)
                continue
            assert source is not None
            raw = source['raw_data']
            if (not isinstance(raw, dict) or not _text(source['source_record_id'])
                    or _text(raw.get('熟製狀態')) != selection.cooking_state
                    or _text(raw.get('烹飪方式')) != selection.cooking_method
                    or _text(raw.get('食用前處理')) != selection.preparation):
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
            result[lid] = {
                'ingredient_id': source['source_record_id'], 'state': STATES[selection.cooking_state],
                'standard_name': observation['standard_name'], 'nutrition_per_100g': values,
                'source': {'provider': 'TFDA', 'source_id': source['source_record_id'],
                    'source_version': import_id, 'source_url': source['source_url'],
                    'food_id': fid, 'import_id': import_id, 'nutrition_id': selection.nutrition_id,
                    'nutrition_source_id': source['source_id'], 'source_key': source['source_key'],
                    'source_sheet': source['source_sheet'], 'source_row': source['source_row'],
                    'observation_source_row': observation['source_row'],
                    'cooking_state': selection.cooking_state, 'cooking_method': selection.cooking_method,
                    'preparation': selection.preparation, 'source_files': deepcopy(files),
                    'raw_source': deepcopy(raw), 'observation_values': {
                        field: observation[field] for field in NUTRIENT_COLUMNS.values()},
                    'observation_quality_issues': deepcopy(observation['quality_issues']),
                    'source_links': [dict(link) for link in source_links],
                    'value_basis': 'included_tfda_source_per_100g'},
            }
    except (SQLAlchemyError, AttributeError):
        raise NutritionDataUnavailable() from None
    return ImportedNutritionData(result, tuple(issues))
