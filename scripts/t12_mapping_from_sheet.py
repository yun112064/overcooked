"""Turn the filled T12 food-issue workbook into config/t12_nutrition_mappings.json.

Reads sheet 「對照建議」 (rows marked 同意 use 建議值, rows marked 改用 use 改用),
or the older sheet 「食材問題清單」 (處理方式 = 採用指定對照).  A FOODxxxxxx value maps nutrition to that
Food_ID; any other value is a TFDA sample ID in the same Food_ID (or, written
as ``FOODxxxxxx:SAMPLE``, in that Food_ID).  Every target is checked against
the All_Food workbook before writing; nothing is approved without --approved-by.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nutrition_planning.nutrition_mapping import DEFAULT_PATH, SCHEMA_VERSION, parse_nutrition_mappings  # noqa: E402

FOOD = re.compile(r'FOOD\d{6}')


def rows(path, sheet):
    book = load_workbook(path, read_only=True, data_only=True)
    it = book[sheet].iter_rows(values_only=True)
    header = next(it)
    return [dict(zip(header, r)) for r in it if any(v is not None for v in r)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sheet', required=True, help='Filled 「T12食材問題與跨組影響」 workbook')
    parser.add_argument('--all-food', required=True, help='All_Food_ID_1018.xlsx used by the current import')
    parser.add_argument('--approved-by', required=True)
    parser.add_argument('--version', required=True, help='New mapping version, e.g. t12-nutrition-mapping-2026-11-02-v1')
    parser.add_argument('--output', default=str(DEFAULT_PATH))
    args = parser.parse_args()
    all_rows = rows(args.all_food, 'All')
    with_energy = {r['Food_ID'] for r in all_rows if r['熱量(kcal/100g)'] is not None
                   and not str(r['計算方式']).startswith('recipe_ingredients')}
    names = {r['Food_ID']: r['標準食材名稱'] for r in all_rows}
    samples = {str(r['來源資料ID']) for r in rows(args.all_food, '原始來源與判定') if r['資料來源'] == 'TFDA'}
    out, problems = [], []
    book_sheets = load_workbook(args.sheet, read_only=True).sheetnames
    if '對照建議' in book_sheets:
        # 「T12營養對照_待確認」：同意 → 建議值；改用 → 改用欄。
        selected = []
        for row in rows(args.sheet, '對照建議'):
            decision = row.get('你的確認')
            value = row.get('建議值') if decision == '同意' else row.get('改用') if decision == '改用' else None
            if value:
                selected.append((row, str(value).strip(), row.get('理由')))
    else:
        selected = [(row, str(row.get('指定 Food_ID／品項') or '').strip(), None)
                    for row in rows(args.sheet, '食材問題清單')
                    if row.get('處理方式') == '採用指定對照' and row.get('指定 Food_ID／品項')]
    for row, target, reason in selected:
        food_id = str(row['食譜 Food_ID'])
        label = row.get('標準名稱') or row.get('名稱')
        nutrition_food, record = food_id, None
        if ':' in target:
            nutrition_food, record = (part.strip() for part in target.split(':', 1))
        elif FOOD.fullmatch(target):
            nutrition_food = target
        else:
            record = target
        if not FOOD.fullmatch(nutrition_food) or nutrition_food not in names:
            problems.append(f'{food_id} {label}: 找不到 Food_ID {nutrition_food}')
            continue
        if record is None and nutrition_food not in with_energy:
            problems.append(f'{food_id} {label}: {nutrition_food} 沒有 TFDA／USDA 熱量資料')
            continue
        if record is not None and record not in samples:
            problems.append(f'{food_id} {label}: 找不到 TFDA 樣品 {record}')
            continue
        out.append({'food_id': food_id, 'name': label, 'nutrition_food_id': nutrition_food,
                    'nutrition_name': names.get(nutrition_food), 'source_record_id': record,
                    'status': 'approved',
                    'basis': '；'.join(str(x) for x in (reason, row.get('備註')) if x) or '使用者於 T12 對照表指定',
                    'approved_by': args.approved_by, 'approved_on': date.today().isoformat()})
    config = {'schema_version': SCHEMA_VERSION, 'version': args.version,
              'scope': '只用於 T12 營養計算；食譜 Food_ID、T07 禁忌、T11 知識、T13 價格都不受影響。',
              'source_sheet': Path(args.sheet).name, 'mappings': out}
    parse_nutrition_mappings(config)  # same validation as runtime
    for problem in problems:
        print('略過：' + problem, file=sys.stderr)
    if problems:
        print(f'{len(problems)} 筆未寫入，請修正後重跑。', file=sys.stderr)
        return 2
    Path(args.output).write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'written': len(out), 'output': args.output, 'version': args.version}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
