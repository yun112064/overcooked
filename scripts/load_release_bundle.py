"""把 nutrition-v1 營養證據 bundle 載入 DEMO 資料庫（單一交易、可重複執行）。

用法：
  python scripts/load_release_bundle.py --bundle release_data/nutrition-v1 [--dry-run]

連線使用 DATABASE_URL（經 app.db.session）。規則：
  - 動資料庫前先以 manifest.json 的 data_sha256（及 policy_sha256）驗證檔案，不符即中止。
  - 依各欄位的 SQLAlchemy 型別轉換值：JSON 原樣傳入（不轉字串）、LargeBinary 由 base64 還原、
    DateTime/Date/Time 由 ISO 字串、Numeric 轉 Decimal、Uuid 轉 UUID；None 一律保持 NULL，不補 0。
  - 整批只有一個交易：任何錯誤全部回滾；--dry-run 做完所有工作後回滾。
  - 已存在的主鍵略過（可重複執行）；food_id 不在 ingredient_aliases、recipe_id 不在 recipes 的列略過並計數。
  - 只新增下列資料表的列；不碰 recipes、recipe_ingredients、ingredient_aliases、價格或任何既有列。
  - bundle 的 recipe_data_releases 描述的是 data/recipe_dataset_1103_V3.xlsx，不是 DEMO 的食譜，
    因此以 active = False 寫入，只作為封面與營養發布版本的外鍵／來源紀錄。
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'backend') not in sys.path:
    sys.path.insert(0, str(ROOT / 'backend'))

# 外鍵順序；只載入這些資料表
LOAD_ORDER = (
    'recipe_data_releases', 'nutrition_imports', 'nutrition_sources', 'food_nutrition',
    'nutrition_source_links', 'nutrition_data_releases', 'nutrition_product_sources',
    'nutrition_source_candidates', 'recipe_covers',
)


class BundleError(ValueError):
    pass


@dataclass
class LoadReport:
    loaded: dict = field(default_factory=dict)
    skipped_existing: dict = field(default_factory=dict)
    skipped_missing_parent: dict = field(default_factory=dict)
    ignored_tables: list = field(default_factory=list)

    def lines(self):
        for name in LOAD_ORDER:
            yield (f'{name}: loaded={self.loaded.get(name, 0)} skipped_existing={self.skipped_existing.get(name, 0)} '
                   f'skipped_missing_parent={self.skipped_missing_parent.get(name, 0)}')
        if self.ignored_tables:
            yield 'not loaded (DEMO keeps its own rows): ' + ', '.join(self.ignored_tables)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_bundle(folder):
    """驗證雜湊後讀取 bundle；只讀檔案，不碰資料庫。"""
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    data_name = manifest.get('data_file') or 'data.json.gz'
    if not manifest.get('data_sha256') or digest(folder / data_name) != manifest['data_sha256']:
        raise BundleError('Bundle hash mismatch: ' + data_name)
    if manifest.get('policy_sha256'):
        policy_name = manifest.get('policy_file') or 'policy.json'
        if digest(folder / policy_name) != manifest['policy_sha256']:
            raise BundleError('Bundle hash mismatch: ' + policy_name)
    with gzip.open(folder / data_name, 'rt', encoding='utf-8') as source:
        payload = json.load(source)
    tables = payload.get('tables') if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise BundleError('Bundle payload has no tables.')
    counts = manifest.get('tables') or {}
    for name, rows in tables.items():
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise BundleError('Payload row shape invalid: ' + name)
        if name in counts and counts[name] != len(rows):
            raise BundleError('Payload row count differs from manifest: ' + name)
    return manifest, tables


def convert_value(column_type, value):
    """依欄位型別轉換匯出的純量；巢狀 JSON 證據原樣保留，絕不轉成字串。"""
    from sqlalchemy import ARRAY, JSON, Date, DateTime, LargeBinary, Numeric, Time, Uuid
    if value is None or isinstance(column_type, JSON):
        return value
    if isinstance(column_type, LargeBinary):
        if isinstance(value, dict) and set(value) == {'__bytes_base64__'}:
            return base64.b64decode(value['__bytes_base64__'], validate=True)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise BundleError('Invalid binary source encoding.')
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if isinstance(column_type, Date):
        return date.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Time):
        return time.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Numeric):
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise BundleError('Non-finite numeric source value.')
        return parsed
    if isinstance(column_type, Uuid):
        return UUID(value) if isinstance(value, str) else value
    if isinstance(column_type, ARRAY):
        return [convert_value(column_type.item_type, entry) for entry in value]
    return value


def convert_row(table, row):
    unknown = set(row) - set(table.c.keys())
    if unknown:
        raise BundleError(f'{table.name}: columns not in DEMO schema: {sorted(unknown)}')
    return {key: convert_value(table.c[key].type, value) for key, value in row.items()}


def bundle_tables():
    from app.models.nutrition import (FoodNutrition, NutritionDataRelease, NutritionImport,
                                      NutritionSource, NutritionSourceLink)
    from app.models.nutrition_candidates import NutritionProductSource, NutritionSourceCandidate
    from app.models.recipe import RecipeCover, RecipeDataRelease
    return {model.__tablename__: model.__table__ for model in (
        RecipeDataRelease, NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink,
        NutritionDataRelease, NutritionProductSource, NutritionSourceCandidate, RecipeCover)}


def load_bundle(db, folder, *, dry_run=False):
    """在呼叫端的 Session 內以單一交易載入；--dry-run 或任何錯誤都整批回滾。"""
    from sqlalchemy import select
    from app.models.recipe import IngredientAlias, Recipe
    _, payload = read_bundle(folder)
    tables = bundle_tables()
    report = LoadReport(ignored_tables=sorted(set(payload) - set(LOAD_ORDER)))
    try:
        known_foods = set(db.scalars(select(IngredientAlias.food_id)))
        known_recipes = set(db.scalars(select(Recipe.recipe_id)))
        present = {}  # 每張表已存在或本次寫入的主鍵，供子表檢查外鍵
        for name in LOAD_ORDER:
            table = tables[name]
            rows = [convert_row(table, row) for row in payload.get(name, [])]
            if name == 'recipe_data_releases':
                # 這筆描述 1103_V3 工作簿而非 DEMO 食譜：只當外鍵／來源紀錄，不設為啟用
                rows = [{**row, 'active': False} for row in rows]
            pk_columns = list(table.primary_key.columns)
            existing = {tuple(r) for r in db.execute(select(*pk_columns))}
            missing_parent, fresh = 0, []
            for row in rows:
                if (('food_id' in table.c and row.get('food_id') not in known_foods)
                        or (name == 'recipe_covers' and row.get('recipe_id') not in known_recipes)
                        or (name == 'nutrition_source_links'
                            and (row.get('nutrition_id'),) not in present['food_nutrition'])):
                    missing_parent += 1
                    continue
                key = tuple(row.get(c.name) for c in pk_columns)
                if key in existing:
                    report.skipped_existing[name] = report.skipped_existing.get(name, 0) + 1
                    continue
                existing.add(key)
                fresh.append(row)
            if fresh:
                db.execute(table.insert(), fresh)
            present[name] = existing
            report.loaded[name] = len(fresh)
            report.skipped_missing_parent[name] = missing_parent
        db.flush()
        if dry_run:
            db.rollback()
        else:
            db.commit()
    except BaseException:
        db.rollback()
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Load the nutrition-v1 evidence bundle into the DEMO database '
                                                 '(DATABASE_URL) in one transaction.')
    parser.add_argument('--bundle', required=True, type=Path, help='bundle folder, e.g. release_data/nutrition-v1')
    parser.add_argument('--dry-run', action='store_true', help='do all work, then roll back')
    args = parser.parse_args(argv)
    read_bundle(args.bundle)  # 連線前先驗證雜湊
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        report = load_bundle(db, args.bundle, dry_run=args.dry_run)
    print('recipe_data_releases: inserted with active=False '
          '(describes data/recipe_dataset_1103_V3.xlsx, not DEMO recipes; FK/provenance parent only)')
    for line in report.lines():
        print(line)
    print('dry run: rolled back, nothing committed' if args.dry_run else 'committed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
