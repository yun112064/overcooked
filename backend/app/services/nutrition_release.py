"""Nutrition-only, immutable source import; never writes recipe or price tables."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unicodedata

from sqlalchemy import insert, inspect, select, text
from sqlalchemy.orm import Session

from app.models.nutrition import (
    FoodNutrition, NutritionDataRelease, NutritionFoodMapping, NutritionImport,
    NutritionSource, NutritionSourceLink,
)
from app.models.recipe import IngredientAlias, RecipeDataRelease, RecipeIngredient
from app.services.nutrition_import import NutritionBundle, NUTRIENT_COLUMNS, parse_nutrition_workbooks

SCHEMA = 't04-nutrition-release-v1'
USAGE = 'observations_only_pending_review'
ROOT = Path(__file__).resolve().parents[3]
T04_TABLES = {'nutrition_data_releases', 'nutrition_imports', 'nutrition_sources',
              'food_nutrition', 'nutrition_source_links', 'nutrition_food_mappings'}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class NutritionReleaseBundle:
    manifest: dict
    manifest_sha256: str
    nutrition: NutritionBundle


def load_bundle(manifest_path: Path, expected_sha256: str) -> NutritionReleaseBundle:
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Nutrition manifest hash mismatch')
    manifest = json.loads(raw)
    if (manifest.get('schema_version') != SCHEMA or manifest.get('usage_status') != USAGE
            or manifest.get('mapping_policy') != 'exact-food-id-candidates-no-approval-v1'
            or set(manifest.get('files', {})) != {'recipes', 'catalog', 'nutrition'}):
        raise ValueError('Unsupported nutrition source policy')
    identity = {k: v for k, v in manifest.items() if k not in {'release_id', 'mapping_version'}}
    identity_hash = digest(identity)[:20]
    if (manifest.get('release_id') != 'nutrition-food1018-v3-' + identity_hash
            or manifest.get('mapping_version') != 'food1018-v3-' + identity_hash):
        raise ValueError('Nutrition release identity does not match the manifest')
    target = (ROOT / manifest['target_recipe_file']).resolve()
    if (target != ROOT / 'data/recipe_dataset_1103_V3.xlsx'
            or hashlib.sha256(target.read_bytes()).hexdigest() != manifest['recipe_source_sha256']):
        raise ValueError('Active recipe source file hash mismatch')
    sources = {}
    for role, info in manifest['files'].items():
        path = (manifest_path.parent / info['file']).resolve()
        if path.parent != manifest_path.parent.resolve() or Path(info['name']).name != info['name']:
            raise ValueError('Nutrition source must remain inside its release folder')
        compressed = path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != info['compressed_sha256']:
            raise ValueError('Compressed nutrition source hash mismatch: ' + role)
        content = gzip.decompress(compressed)
        if len(content) != info['bytes'] or hashlib.sha256(content).hexdigest() != info['sha256']:
            raise ValueError('Original nutrition source hash mismatch: ' + role)
        sources[role] = content
    # Parsing the historical workbook is evidence extraction, not a T01 import.
    with tempfile.TemporaryDirectory() as folder:
        paths = {}
        for role, content in sources.items():
            path = Path(folder) / manifest['files'][role]['name']
            path.write_bytes(content)
            paths[role] = path
        nutrition = parse_nutrition_workbooks(paths['recipes'], paths['catalog'], paths['nutrition'])
    expected_files = {role: {'name': info['name'], 'sha256': info['sha256']}
                      for role, info in manifest['files'].items()}
    if nutrition.source_files != expected_files:
        raise ValueError('Parsed nutrition sources do not match manifest roles')
    return NutritionReleaseBundle(manifest, expected_sha256, nutrition)


def _name(value) -> str:
    return unicodedata.normalize('NFKC', value or '').strip()


def stored_observations(bundle: NutritionReleaseBundle) -> list[dict]:
    # SQL Numeric(24,12) is explicit; the complete source cells remain in raw_data.
    return [{**row, **{field: (row[field].quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN)
                              if row[field] is not None else None)
                       for field in NUTRIENT_COLUMNS.values()}}
            for row in bundle.nutrition.observations]


def mapping_plan(bundle: NutritionReleaseBundle, catalog: dict[str, str]) -> list[dict]:
    observations = {r['nutrition_id']: r for r in bundle.nutrition.observations}
    sources = {r['source_id']: r for r in bundle.nutrition.sources}
    rows = []
    for link in bundle.nutrition.links:
        observation = observations[link['nutrition_id']]
        source = sources[link['source_id']]
        if source['provider'] != 'TFDA':
            continue
        fid = observation['food_id']
        if fid not in catalog:
            raise ValueError('Nutrition Food_ID absent from the active T01 catalog: ' + fid)
        identity = [bundle.nutrition.import_id, bundle.manifest['recipe_release_id'],
                    fid, observation['nutrition_id'], source['source_id'], bundle.manifest['mapping_version']]
        rows.append({
            'mapping_id': digest(identity), 'import_id': bundle.nutrition.import_id,
            'recipe_release_id': bundle.manifest['recipe_release_id'], 'food_id': fid,
            'nutrition_id': observation['nutrition_id'], 'source_id': source['source_id'],
            'cooking_state': observation['cooking_state'], 'cooking_method': observation['cooking_method'],
            'preparation': observation['preparation'], 'approval_status': 'pending_review',
            'mapping_version': bundle.manifest['mapping_version'],
            'evidence': {'policy': bundle.manifest['mapping_policy'], 'manifest_sha256': bundle.manifest_sha256,
                'current_t01_name': catalog[fid], 'nutrition_name': observation['standard_name'],
                'name_matches': _name(catalog[fid]) == _name(observation['standard_name']),
                'source_key': source['source_key'], 'included_in_calculation': link['included_in_calculation'],
                'source_state': source['raw_data'].get('熟製狀態'),
                'source_method': source['raw_data'].get('烹飪方式'),
                'approval_inherited': False},
        })
    return rows


def _check_rows(session: Session, model, conditions, expected: list[dict], keys: tuple[str, ...]):
    actual = [dict(r) for r in session.execute(select(model.__table__).where(*conditions)).mappings()]
    expected_by_id = {tuple(row[k] for k in keys): row for row in expected}
    actual_by_id = {tuple(row[k] for k in keys): row for row in actual}
    if set(actual_by_id) != set(expected_by_id):
        raise ValueError('Stored source row identities differ: ' + model.__tablename__)
    for key, row in expected_by_id.items():
        if any(actual_by_id[key][field] != value for field, value in row.items()):
            raise ValueError('Stored source values differ: ' + model.__tablename__ + ' / ' + str(key))


def comparable_report(report):
    """Keep the first validation timestamp; it is not a source-content identity."""
    value = deepcopy(report)
    if isinstance(value, dict) and isinstance(value.get('recipe_quality'), dict):
        value['recipe_quality'].pop('generated_at', None)
    return value


def verify_installed(session: Session, bundle: NutritionReleaseBundle, mappings: list[dict], *, verify_mappings=True) -> None:
    n = bundle.nutrition
    imported = session.get(NutritionImport, n.import_id)
    if (not imported or imported.source_files != n.source_files
            or comparable_report(imported.report) != comparable_report(n.report)):
        raise ValueError('Stored nutrition import metadata differs')
    _check_rows(session, FoodNutrition, [FoodNutrition.import_id == n.import_id], stored_observations(bundle), ('nutrition_id',))
    _check_rows(session, NutritionSource, [NutritionSource.import_id == n.import_id], n.sources, ('source_id',))
    source_ids = [r['source_id'] for r in n.sources]
    _check_rows(session, NutritionSourceLink, [NutritionSourceLink.source_id.in_(source_ids)], n.links,
                ('nutrition_id', 'source_id'))
    if not verify_mappings:
        return
    _check_rows(session, NutritionFoodMapping,
                [NutritionFoodMapping.import_id == n.import_id,
                 NutritionFoodMapping.recipe_release_id == bundle.manifest['recipe_release_id'],
                 NutritionFoodMapping.mapping_version == bundle.manifest['mapping_version']],
                mappings, ('mapping_id',))


def install(session: Session, bundle: NutritionReleaseBundle) -> dict:
    """Caller owns the transaction. No recipe, price, estimate, or knowledge writes."""
    if session.bind.dialect.name == 'postgresql':
        url = session.bind.engine.url
        if (url.host != '127.0.0.1' or url.port != 55435
                or session.scalar(text('SELECT current_database()')) not in {
                    'meal_planner_1102_v3', 'meal_planner_1102_v3_t04_test'}):
            raise ValueError('Nutrition writes are restricted to the dedicated V3 server/databases')
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('overcooked:t04:release',0))"))
    manifest, n = bundle.manifest, bundle.nutrition
    release = session.scalar(select(RecipeDataRelease).where(
        RecipeDataRelease.release_id == manifest['recipe_release_id']).with_for_update())
    if (not release or not release.active or release.source_sha256 != manifest['recipe_source_sha256']):
        raise ValueError('Active T01 release does not match nutrition binding')
    catalog = dict(session.execute(select(IngredientAlias.food_id, IngredientAlias.canonical_name).where(
        IngredientAlias.recipe_release_id == release.release_id)).all())
    unknown = {row['food_id'] for row in n.observations} - set(catalog)
    if unknown:
        raise ValueError('Source nutrition contains unknown Food_IDs: ' + ','.join(sorted(unknown)))
    mappings = mapping_plan(bundle, catalog)
    counts = {'food_nutrition': len(n.observations), 'nutrition_sources': len(n.sources),
              'nutrition_source_links': len(n.links), 'nutrition_food_mappings': len(mappings)}
    existing = session.get(NutritionDataRelease, manifest['release_id'])
    active = session.scalar(select(NutritionDataRelease).where(
        NutritionDataRelease.recipe_release_id == release.release_id, NutritionDataRelease.active.is_(True)))
    if active and active.release_id != manifest['release_id']:
        raise ValueError('Another nutrition release is active; refusing an unscoped replacement')
    if existing:
        if (not existing.active or existing.manifest_sha256 != bundle.manifest_sha256
                or existing.source_manifest != manifest or existing.row_counts != counts
                or existing.import_id != n.import_id or existing.mapping_version != manifest['mapping_version']
                or existing.recipe_release_id != release.release_id
                or existing.recipe_source_sha256 != release.source_sha256 or existing.usage_status != USAGE):
            raise ValueError('Stored nutrition release metadata differs')
        verify_installed(session, bundle, mappings)
        status = 'unchanged'
    else:
        if session.get(NutritionImport, n.import_id):
            verify_installed(session, bundle, mappings, verify_mappings=False)
        else:
            session.add(NutritionImport(import_id=n.import_id, source_files=n.source_files,
                                        report=n.report, imported_at=datetime.now(timezone.utc)))
            session.flush()
            for model, rows in ((NutritionSource, n.sources), (FoodNutrition, stored_observations(bundle)),
                                (NutritionSourceLink, n.links)):
                for offset in range(0, len(rows), 250):
                    session.execute(insert(model).execution_options(render_nulls=True), rows[offset:offset + 250])
                session.flush()
        for offset in range(0, len(mappings), 250):
            session.execute(insert(NutritionFoodMapping).execution_options(render_nulls=True), mappings[offset:offset + 250])
        session.flush()
        session.add(NutritionDataRelease(release_id=manifest['release_id'], import_id=n.import_id,
            recipe_release_id=release.release_id, recipe_source_sha256=release.source_sha256,
            mapping_version=manifest['mapping_version'], manifest_sha256=bundle.manifest_sha256,
            source_manifest=manifest, row_counts=counts, usage_status=USAGE,
            active=True, imported_at=datetime.now(timezone.utc)))
        session.flush()
        verify_installed(session, bundle, mappings)
        status = 'installed'
    used = set(session.scalars(select(RecipeIngredient.food_id).where(
        RecipeIngredient.recipe_release_id == release.release_id)).all())
    observed = {row['food_id'] for row in n.observations}
    name_differences = [{'food_id': r['food_id'], 'current_name': catalog[r['food_id']],
                         'nutrition_name': r['standard_name'], 'source_row': r['source_row']}
                        for r in n.observations if _name(catalog[r['food_id']]) != _name(r['standard_name'])]
    return {'status': status, 'release_id': manifest['release_id'], 'import_id': n.import_id,
        'recipe_release_id': release.release_id, 'mapping_version': manifest['mapping_version'],
        'manifest_sha256': bundle.manifest_sha256, 'usage_status': USAGE, 'counts': counts,
        'recipe_food_ids': len(used), 'recipe_food_ids_with_observations': len(used & observed),
        'recipe_food_ids_without_observations': [{'food_id': fid, 'name': catalog[fid]} for fid in sorted(used - observed)],
        'name_differences': name_differences, 'missing_nutrients': n.report['missing_nutrients'],
        'source_count_mismatches': n.report['reconciliation']['source_count_mismatches'],
        'providers': dict(Counter(r['provider'] for r in n.sources)), 'approved_mappings': 0,
        'cooking_estimates_imported': 0, 'knowledge_imported': 0}


def recipe_import_matches(session: Session, release: RecipeDataRelease, imported: NutritionImport,
                          *, mapping_version: str | None = None) -> bool:
    """Legacy same-workbook imports or an explicit hash-bound V3 release; never an implicit latest batch."""
    if not release.active:
        return False
    source = (imported.source_files or {}).get('recipes', {})
    if isinstance(source, dict) and source.get('sha256') == release.source_sha256:
        return True
    if not inspect(session.connection()).has_table(NutritionDataRelease.__tablename__):
        return False
    return session.scalar(select(NutritionDataRelease.release_id).where(
        NutritionDataRelease.import_id == imported.import_id,
        NutritionDataRelease.recipe_release_id == release.release_id,
        NutritionDataRelease.recipe_source_sha256 == release.source_sha256,
        NutritionDataRelease.mapping_version == mapping_version if mapping_version else True,
        NutritionDataRelease.active.is_(True))) is not None
