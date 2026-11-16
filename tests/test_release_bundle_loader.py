"""nutrition-v1 bundle 載入器：雜湊先驗、單一交易、可重複執行、依欄位型別轉換且未知值保持 NULL。

合成 bundle 只用 SQLite；真實 bundle 只做 gzip/json 靜態讀取，不連任何資料庫。
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

import base64
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.nutrition import FoodNutrition, NutritionSource
from app.models.nutrition_candidates import NutritionProductSource, NutritionSourceCandidate
from app.models.recipe import IngredientAlias, Recipe, RecipeCover, RecipeDataRelease, SourceArchive
from app.services.t04_data_feedback import fingerprint

ROOT = Path(__file__).resolve().parents[1]
REAL_BUNDLE = ROOT / 'release_data' / 'nutrition-v1'
_spec = importlib.util.spec_from_file_location('load_release_bundle', ROOT / 'scripts' / 'load_release_bundle.py')
loader = importlib.util.module_from_spec(_spec); sys.modules['load_release_bundle'] = loader
_spec.loader.exec_module(loader)

IMPORT = 'imp1'
RELEASE = 'rel1'
IMAGE = b'\x89PNG\r\n\x1a\n\x00synthetic\xff'


def synthetic_tables():
    source = {'source_id': 's1', 'import_id': IMPORT, 'source_key': 'TFDA_T1', 'provider': 'TFDA',
              'source_record_id': 'T1', 'source_url': 'https://example.invalid', 'source_sheet': '合成',
              'source_row': 7, 'raw_data': {'中文原名': '合成食材', 'nested': {'a': [1, 2]}}}
    return {
        'recipe_data_releases': [{'release_id': RELEASE, 'dataset_version': 'synthetic', 'source_sha256': 'a' * 64,
            'schema_version': 'test', 'source_kind': 'xlsx_import', 'source_uri': 'synthetic.xlsx',
            'row_counts': {'recipes': 1}, 'active': True, 'imported_at': '2026-11-03T10:14:29.886207+00:00'}],
        'nutrition_imports': [{'import_id': IMPORT, 'source_files': {'catalog': {'name': 'x'}}, 'report': {'counts': {}},
            'imported_at': '2026-11-03T13:10:16Z'}],
        'nutrition_sources': [source],
        'food_nutrition': [
            {'nutrition_id': 'n1', 'import_id': IMPORT, 'food_id': 'F1', 'source_row': 1, 'standard_name': '合成食材',
             'cooking_state': '生', 'cooking_method': None, 'preparation': None, 'calculation_method': None,
             'energy_kcal': None, 'protein_g': '3.400000000000', 'fat_g': None, 'carbohydrate_g': None, 'sugars_g': None,
             'sodium_mg': None, 'dietary_fiber_g': None, 'nutrition_complete': False, 'quality_issues': [], 'raw_data': {}},
            {'nutrition_id': 'n2', 'import_id': IMPORT, 'food_id': 'F_UNKNOWN', 'source_row': 2, 'standard_name': '未知',
             'cooking_state': '生', 'nutrition_complete': False, 'quality_issues': [], 'raw_data': {}}],
        'nutrition_source_links': [{'nutrition_id': 'n1', 'source_id': 's1', 'included_in_calculation': True},
                                   {'nutrition_id': 'n2', 'source_id': 's1', 'included_in_calculation': True}],
        'nutrition_data_releases': [{'release_id': 'nr1', 'import_id': IMPORT, 'recipe_release_id': RELEASE,
            'recipe_source_sha256': 'a' * 64, 'mapping_version': 'm1', 'manifest_sha256': 'b' * 64,
            'source_manifest': {}, 'row_counts': {}, 'usage_status': 'observations_only_pending_review',
            'active': True, 'imported_at': '2026-11-03T13:10:18+00:00'}],
        'nutrition_product_sources': [{'product_source_id': 'p1', 'recipe_release_id': RELEASE, 'food_id': 'F_UNKNOWN',
            'source_name': '合成產品', 'source_url': 'https://example.invalid/p', 'evidence_sha256': 'c' * 64,
            'per_100g': {'energy_kcal': '880'}, 'provenance': {}, 'created_at': '2026-11-03T15:27:23+00:00'}],
        'nutrition_source_candidates': [{'candidate_id': 'c1', 'recipe_release_id': RELEASE, 'nutrition_import_id': IMPORT,
            'food_id': 'F1', 'source_id': 's1', 'source_fingerprint': fingerprint(source), 'policy_version': 'v1',
            'match_evidence': {'automatic_approval': False}, 'review_status': 'pending_review',
            'created_at': '2026-11-03T16:04:35+00:00'}],
        'recipe_covers': [{'recipe_id': 'R1', 'recipe_release_id': RELEASE, 'filename': 'R1.png', 'image_url': 'https://example.invalid/i',
            'sha256': hashlib.sha256(IMAGE).hexdigest(), 'media_type': 'image/png',
            'image_bytes': {'__bytes_base64__': base64.b64encode(IMAGE).decode()}, 'approved_at': '2026-11-04T03:51:11+00:00'},
            {'recipe_id': 'R_UNKNOWN', 'recipe_release_id': RELEASE, 'filename': 'x.png', 'image_url': 'https://example.invalid/x',
             'sha256': 'd' * 64, 'media_type': 'image/png', 'image_bytes': {'__bytes_base64__': ''},
             'approved_at': '2026-11-04T03:51:11+00:00'}],
        'recipes': [{'recipe_id': 'IGNORED'}],
    }


def write_bundle(folder, tables, *, corrupt=False):
    folder.mkdir(parents=True, exist_ok=True)
    data = folder / 'data.json.gz'
    with gzip.open(data, 'wt', encoding='utf-8') as out:
        json.dump({'tables': tables}, out, ensure_ascii=False)
    sha = hashlib.sha256(data.read_bytes()).hexdigest()
    manifest = {'data_file': 'data.json.gz', 'data_sha256': '0' * 64 if corrupt else sha,
                'tables': {name: len(rows) for name, rows in tables.items()}}
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return folder


@pytest.fixture
def engine():
    e = create_engine('sqlite://')
    models = [SourceArchive, IngredientAlias, Recipe, *[loader.bundle_tables()[n] for n in loader.LOAD_ORDER]]
    Base.metadata.create_all(e, tables=[getattr(m, '__table__', m) for m in models])
    with Session(e) as s:
        s.add(SourceArchive(raw_id='raw1', source_name='t', source_url='https://example.invalid', source_recipe_id='R1',
            recipe_name_raw='合成菜', servings_raw='2人份', ingredients_raw='x', steps_raw='x', tags_raw='',
            content_hash='b' * 64, import_batch_id='t'))
        s.add(IngredientAlias(food_id='F1', canonical_name='合成食材', aliases=[]))
        s.add(Recipe(recipe_id='R1', raw_id='raw1', standard_dish_id='D1', recipe_name='合成菜', normalized_name='合成菜',
            servings_raw='2人份', servings_status='parsed', category='主菜', meal_type='dinner', cuisine='t',
            source_name='t', source_url='https://example.invalid'))
        s.commit()
    yield e
    e.dispose()


def counts(engine):
    with Session(engine) as s:
        return {n: s.scalar(select(func.count()).select_from(loader.bundle_tables()[n])) for n in loader.LOAD_ORDER}


def test_hash_mismatch_aborts_before_any_insert(engine, tmp_path):
    folder = write_bundle(tmp_path / 'b', synthetic_tables(), corrupt=True)
    with Session(engine) as s, pytest.raises(loader.BundleError, match='hash mismatch'):
        loader.load_bundle(s, folder)
    assert set(counts(engine).values()) == {0}


def test_failure_in_last_table_rolls_back_everything(engine, tmp_path):
    tables = synthetic_tables()
    tables['recipe_covers'][0]['not_a_column'] = 1
    folder = write_bundle(tmp_path / 'b', tables)
    with Session(engine) as s, pytest.raises(loader.BundleError, match='recipe_covers'):
        loader.load_bundle(s, folder)
    assert set(counts(engine).values()) == {0}
    with Session(engine) as s:  # 既有列不受影響
        assert s.scalar(select(func.count()).select_from(Recipe)) == 1


def test_dry_run_leaves_tables_empty(engine, tmp_path):
    folder = write_bundle(tmp_path / 'b', synthetic_tables())
    with Session(engine) as s:
        report = loader.load_bundle(s, folder, dry_run=True)
    assert report.loaded['nutrition_sources'] == 1
    assert set(counts(engine).values()) == {0}


def test_load_converts_by_type_skips_orphans_and_is_idempotent(engine, tmp_path):
    folder = write_bundle(tmp_path / 'b', synthetic_tables())
    with Session(engine) as s:
        first = loader.load_bundle(s, folder)
    assert first.loaded == {'recipe_data_releases': 1, 'nutrition_imports': 1, 'nutrition_sources': 1, 'food_nutrition': 1,
        'nutrition_source_links': 1, 'nutrition_data_releases': 1, 'nutrition_product_sources': 0,
        'nutrition_source_candidates': 1, 'recipe_covers': 1}
    assert first.skipped_missing_parent['food_nutrition'] == 1
    assert first.skipped_missing_parent['nutrition_source_links'] == 1
    assert first.skipped_missing_parent['nutrition_product_sources'] == 1
    assert first.skipped_missing_parent['recipe_covers'] == 1
    assert first.ignored_tables == ['recipes']
    with Session(engine) as s:
        second = loader.load_bundle(s, folder)
    assert set(second.loaded.values()) == {0}
    assert second.skipped_existing['nutrition_sources'] == 1
    with Session(engine) as s:
        assert s.get(RecipeDataRelease, RELEASE).active is False
        cover = s.get(RecipeCover, ('R1', RELEASE))
        assert cover.image_bytes == IMAGE
        food = s.get(FoodNutrition, 'n1')
        assert s.scalar(select(func.count()).select_from(FoodNutrition).where(FoodNutrition.energy_kcal.is_(None))) == 1
        assert food.energy_kcal is None and food.fat_g is None
        assert isinstance(food.protein_g, Decimal) and food.protein_g == Decimal('3.4')
        source = s.get(NutritionSource, 's1')
        assert source.source_row == 7 and source.raw_data == {'中文原名': '合成食材', 'nested': {'a': [1, 2]}}
        assert isinstance(s.get(NutritionSourceCandidate, 'c1').match_evidence, dict)
        assert s.get(NutritionProductSource, 'p1') is None


def test_cli_help_needs_no_database():
    with pytest.raises(SystemExit) as exit_info:
        loader.main(['--help'])
    assert exit_info.value.code == 0


# ---- 真實 bundle：只讀檔案 ----

@pytest.fixture(scope='module')
def real():
    return loader.read_bundle(REAL_BUNDLE)


def test_real_manifest_sha_and_counts(real):
    manifest, tables = real
    assert hashlib.sha256((REAL_BUNDLE / 'data.json.gz').read_bytes()).hexdigest() == manifest['data_sha256']
    assert {n: len(r) for n, r in tables.items()} == manifest['tables']


def test_real_rows_fit_demo_schema(real):
    _, tables = real
    models = loader.bundle_tables()
    for name in loader.LOAD_ORDER:
        for row in tables[name]:
            loader.convert_row(models[name], row)


def test_real_candidate_fingerprints_survive_type_conversion(real):
    _, tables = real
    table = NutritionSource.__table__
    sources = {row['source_id']: loader.convert_row(table, row) for row in tables['nutrition_sources']}
    candidates = tables['nutrition_source_candidates']
    assert len(candidates) == 509
    matched = sum(fingerprint({c.name: sources[cand['source_id']].get(c.name) for c in table.columns})
                  == cand['source_fingerprint'] for cand in candidates)
    assert matched == 509


def test_real_unknown_energy_stays_none(real):
    _, tables = real
    table = FoodNutrition.__table__
    rows = [loader.convert_row(table, row) for row in tables['food_nutrition']]
    assert len(rows) == 1996
    assert sum(row['energy_kcal'] is None for row in rows) == 312
    assert all(row['energy_kcal'] is None or isinstance(row['energy_kcal'], Decimal) for row in rows)
