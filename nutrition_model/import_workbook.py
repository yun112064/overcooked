"""Build offline nutrition tables from the team's 食材營養成分表.xlsx."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

import pandas as pd
from cooked_nutrition_service import cooked_metadata_for

BASE = Path(__file__).parent / 'data'
SOURCE = BASE / 'source' / '食材營養成分表.xlsx'
COLUMNS = {
    '熱量(kcal/100g)': 'energy_kcal',
    '碳水化合物(g/100g)': 'carbohydrate_g',
    '蛋白質(g/100g)': 'protein_g',
    '脂肪(g/100g)': 'fat_g',
    '糖(g/100g)': 'sugars_g',
    '鈉(mg/100g)': 'sodium_mg',
    '膳食纖維(g/100g)': 'dietary_fiber_g',
}


def clean(value):
    if pd.isna(value):
        return None
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def number(value):
    value = clean(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def import_workbook(path=SOURCE):
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    book = pd.ExcelFile(source)
    required_sheets = {'食材營養成分', '食材ID主檔'}
    if not required_sheets.issubset(book.sheet_names):
        raise ValueError('Missing required workbook sheets')
    foods_df = pd.read_excel(book, sheet_name='食材營養成分')
    ids_df = pd.read_excel(book, sheet_name='食材ID主檔')
    required = set(COLUMNS) | {'來源食品ID', '中文食品名稱', '資料來源', '來源紀錄ID',
                                '來源生熟狀態', '來源版本', '來源描述', '來源網址'}
    if not required.issubset(foods_df.columns):
        raise ValueError('Missing required food columns')
    if not {'ingredient_id', 'canonical_name', 'suggested_name'}.issubset(ids_df.columns):
        raise ValueError('Missing ingredient ID columns')
    foods = [{key: clean(value) for key, value in row.items()}
             for row in foods_df.to_dict(orient='records')]
    ids = [{key: clean(value) for key, value in row.items()}
           for row in ids_df.to_dict(orient='records')]
    food_ids = [str(row['來源食品ID']) for row in foods]
    if any(not value or value == 'None' for value in food_ids) or len(set(food_ids)) != len(food_ids):
        raise ValueError('Missing or duplicate source food IDs')
    if any(row['資料來源'] not in ('TFDA', 'USDA') for row in foods):
        raise ValueError('Unknown nutrition source')
    existing_path = BASE / 'ingredient_master.json'
    old = {r['ingredient_id']: r for r in json.loads(existing_path.read_text(encoding='utf-8'))} if existing_path.exists() else {}
    tfda = []
    for food in foods:
        if food['資料來源'] != 'TFDA':
            continue
        record_id = str(food['來源紀錄ID'])
        values = {field: number(food[column]) for column, field in COLUMNS.items()}
        missing = [field for field, value in values.items() if value is None]
        record = {
            'ingredient_id': record_id,
            'source_food_id': str(food['來源食品ID']),
            'standard_name': str(food['中文食品名稱']),
            # Aliases are lookup metadata from the prior TFDA import, not nutrient data.
            'aliases': old.get(record_id, {}).get('aliases', []),
            'state': str(food['來源生熟狀態']),
            'nutrition_per_100g': values,
            'nutrition_complete': not missing,
            'missing_fields': missing,
            'source': {'provider': 'TFDA', 'source_id': record_id,
                       'source_food_id': str(food['來源食品ID']),
                       'source_file': '食材營養成分表.xlsx', 'source_sheet': '食材營養成分',
                       'source_version': food['來源版本'], 'source_url': food['來源網址']},
        }
        if food['來源描述'] is not None:
            record['description'] = food['來源描述']
        if record['state'] == 'cooked':
            record.update(cooked_metadata_for(record_id))
        tfda.append(record)
    if len({r['ingredient_id'] for r in tfda}) != len(tfda):
        raise ValueError('Duplicate TFDA record IDs')
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != SOURCE.resolve():
        shutil.copy2(source, SOURCE)
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    manifest = {'source_file': SOURCE.name, 'sha256': digest,
                'row_counts': {'TFDA': len(tfda), 'USDA': len(foods) - len(tfda),
                               'ingredient_ids': len(ids)},
                'nutrition_fields': list(COLUMNS.values())}
    write_json(BASE / 'workbook_foods.json', foods)
    write_json(BASE / 'ingredient_id_master.json', ids)
    write_json(BASE / 'ingredient_master.json', tfda)
    write_json(BASE / 'workbook_manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=str(SOURCE))
    args = parser.parse_args()
    print(json.dumps(import_workbook(args.input), ensure_ascii=False, indent=2))
