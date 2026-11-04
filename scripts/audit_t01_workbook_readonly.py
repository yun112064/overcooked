"""Inspect a recipe workbook and importer contract without importing application code."""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def records(path: Path | bytes):
    workbook = load_workbook(BytesIO(path) if isinstance(path, bytes) else path, read_only=True, data_only=True)
    output = {}
    try:
        for sheet in workbook:
            iterator = sheet.iter_rows(values_only=True)
            headers = list(next(iterator, ()))
            rows = []
            for number, values in enumerate(iterator, 2):
                if any(v is not None and str(v).strip() for v in values):
                    rows.append({'__row__': number, **dict(zip(headers, values))})
            output[sheet.title] = {'headers': headers, 'rows': rows}
    finally:
        workbook.close()
    return output


def numeric(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def audit(path: Path, baseline: Path | None):
    content = path.read_bytes()
    source_hash = hashlib.sha256(content).hexdigest()
    snapshot = ROOT / '.test-runtime' / 't01-1103-v2' / (source_hash + '.xlsx')
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        snapshot.write_bytes(content)
    elif hashlib.sha256(snapshot.read_bytes()).hexdigest() != source_hash:
        raise ValueError('Existing immutable audit snapshot has a mismatched hash')
    sheets = records(content)
    contract = {}
    module = ast.parse((ROOT / 'backend/app/services/recipe_import.py').read_text(encoding='utf-8'))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'REQUIRED_COLUMNS' for t in node.targets):
            contract = ast.literal_eval(node.value)
    checks = []
    for sheet, columns in contract.items():
        missing = sorted(columns - set(sheets.get(sheet, {}).get('headers', [])))
        if missing:
            checks.append({'issue': 'missing_columns', 'sheet': sheet, 'columns': missing})
    primary = {'source_archive':'raw_id', 'recipes':'recipe_id', 'recipe_steps':'step_id',
               'recipe_ingredients':'recipe_ingredient_id', 'ingredient_aliases':'alias_id', 'allergens':'allergen_id'}
    for sheet, key in primary.items():
        data = sheets.get(sheet, {}).get('rows', [])
        counts = Counter(r.get(key) for r in data)
        duplicates = [{'id': str(k), 'count':v} for k,v in counts.items() if k is not None and v > 1]
        blank_rows = [r['__row__'] for r in data if r.get(key) is None or not str(r[key]).strip()]
        if duplicates or blank_rows:
            checks.append({'issue':'primary_key', 'sheet':sheet, 'key':key, 'duplicates':duplicates, 'blank_rows':blank_rows})
    catalog = sheets.get('標準食材整合', {}).get('rows', [])
    catalog_map = defaultdict(set)
    for row in catalog:
        catalog_map[str(row.get('Food_ID') or '').strip()].add(str(row.get('合併標準食材名稱') or '').strip())
    recipes = sheets.get('recipes', {}).get('rows', [])
    ingredients = sheets.get('recipe_ingredients', {}).get('rows', [])
    steps = sheets.get('recipe_steps', {}).get('rows', [])
    recipe_ids = {r.get('recipe_id') for r in recipes}
    raw_ids = {r.get('raw_id') for r in sheets.get('source_archive', {}).get('rows', [])}
    orphans = {'recipe_raw_ids': sorted({str(r.get('raw_id')) for r in recipes if r.get('raw_id') not in raw_ids}),
               'step_recipe_ids': sorted({str(r.get('recipe_id')) for r in steps if r.get('recipe_id') not in recipe_ids}),
               'ingredient_recipe_ids': sorted({str(r.get('recipe_id')) for r in ingredients if r.get('recipe_id') not in recipe_ids}),
               'ingredient_food_ids': sorted({str(r.get('Food_ID')) for r in ingredients if str(r.get('Food_ID') or '').strip() not in catalog_map})}
    orphans['alias_food_ids'] = sorted({str(r.get('Food_ID')) for r in sheets.get('ingredient_aliases',{}).get('rows',[])
                                      if str(r.get('Food_ID') or '').strip() not in catalog_map})
    weights = []
    name_mismatches = []
    for row in ingredients:
        weight = numeric(row.get('weight_g'))
        if weight is None or weight <= 0:
            weights.append({'row':row['__row__'], 'line_id':row.get('recipe_ingredient_id'),
                            'food_id':row.get('Food_ID'), 'raw_text':row.get('raw_text'),
                            'weight_g':row.get('weight_g'), 'reason':'missing_or_invalid' if weight is None else 'nonpositive'})
        fid = str(row.get('Food_ID') or '').strip()
        name = str(row.get('canonical_name') or '').strip()
        if fid in catalog_map and name not in catalog_map[fid]:
            name_mismatches.append({'row':row['__row__'], 'food_id':fid, 'name':name, 'catalog_names':sorted(catalog_map[fid])})
    for sheet, keys in [('recipe_steps',('recipe_id','step_no')),('recipe_ingredients',('recipe_id','sequence'))]:
        group = Counter(tuple(str(r.get(k)) for k in keys) for r in sheets.get(sheet,{}).get('rows',[]))
        dup = [{'values':list(k),'count':v} for k,v in group.items() if v > 1]
        if dup:
            checks.append({'issue':'duplicate_sequence','sheet':sheet,'keys':keys,'duplicates':dup})
    names = defaultdict(list)
    for row in recipes:
        names[str(row.get('recipe_name') or '').strip()].append(row.get('recipe_id'))
    name_duplicates = {k:v for k,v in names.items() if len(v)>1}
    servings = []
    for row in recipes:
        raw = str(row.get('servings_raw') or '')
        numbers = re.findall(r'\d+(?:\.\d+)?',raw)
        if len(numbers)!=1 or Decimal(numbers[0])<=0:
            servings.append({'recipe_id':row.get('recipe_id'),'servings_raw':raw})
    nutrient_columns = ['protein_g','carbohydrates_g','fat_g','sugar_g','sodium_mg']
    result = {'source_file':str(path.resolve()),'sha256':source_hash,'immutable_audit_snapshot':str(snapshot),
        'database_written':False, 'importer_executed':False,
        'sheets':{s:{'rows':len(d['rows']),'headers':d['headers']} for s,d in sheets.items()},
        'contract_issues':checks, 'orphan_references':orphans,
        'catalog_unique_food_ids':len(catalog_map),
        'catalog_duplicate_food_ids':[{'food_id':k,'count':v} for k,v in Counter(str(r.get('Food_ID') or '').strip() for r in catalog).items() if v>1],
        'catalog_ambiguous_food_ids':{k:sorted(v) for k,v in catalog_map.items() if len(v)>1},
        'recipe_unique_food_ids':len({r.get('Food_ID') for r in ingredients}),
        'missing_ingredient_recipes':sorted(recipe_ids-{r.get('recipe_id') for r in ingredients}),
        'missing_step_recipes':sorted(recipe_ids-{r.get('recipe_id') for r in steps}),
        'weight_issues':weights,'catalog_name_mismatches':name_mismatches,
        'duplicate_recipe_names':name_duplicates,'servings_requiring_review':servings,
        'unit_counts':dict(Counter(str(r.get('unit')) for r in ingredients)),
        'category_counts':dict(Counter(str(r.get('category')) for r in recipes)),
        'source_counts':dict(Counter(str(r.get('source_name')) for r in recipes)),
        'duplicate_recipe_records':[{k:r.get(k) for k in ('recipe_id','recipe_name','source_url','servings_raw')}
                                    for r in recipes if r.get('recipe_name') in name_duplicates],
        'special_serving_recipes':[{k:r.get(k) for k in ('recipe_id','recipe_name','category','servings_raw')}
                                  for r in recipes if any(i['recipe_id']==r.get('recipe_id') for i in servings)],
        'g_amount_weight_disagreements':[{k:r.get(k) for k in ('recipe_ingredient_id','raw_text','amount','weight_g')}
                                         for r in ingredients if r.get('unit')=='g' and numeric(r.get('amount'))!=numeric(r.get('weight_g'))],
        'non_g_lines_with_explicit_grams':sum(r.get('unit')!='g' and numeric(r.get('weight_g')) is not None for r in ingredients),
        'nutrient_presence':{key:{'nonblank':sum(r.get(key) is not None for r in ingredients),
                                'zero':sum(numeric(r.get(key))==0 for r in ingredients),
                                'nonnumeric':[r['__row__'] for r in ingredients if r.get(key) is not None and numeric(r.get(key)) is None]}
                             for key in nutrient_columns},
        'blank_required_cells':{s:{key:sum(r.get(key) is None or str(r.get(key)).strip()=='' for r in d['rows'])
                                    for key in d['headers']} for s,d in sheets.items()},
        'sample_rows':{s:d['rows'][:2] for s,d in sheets.items()}}
    if baseline and baseline.is_file():
        old=records(baseline)
        comparison={}
        for sheet,key in primary.items():
            a={str(r.get(key)):r for r in old.get(sheet,{}).get('rows',[])}
            b={str(r.get(key)):r for r in sheets.get(sheet,{}).get('rows',[])}
            changed=Counter()
            for identity in a.keys()&b.keys():
                for column in (a[identity].keys() | b[identity].keys())-{'__row__'}:
                    if a[identity].get(column)!=b[identity].get(column): changed[column]+=1
            comparison[sheet]={'old_rows':len(a),'new_rows':len(b),'added_ids':len(b.keys()-a.keys()),
                               'removed_ids':len(a.keys()-b.keys()),'changed_cells_by_column':dict(changed)}
        result['baseline_comparison']={'file':str(baseline),'tables':comparison}
        old_catalog={str(r.get('Food_ID')):r.get('合併標準食材名稱') for r in old.get('標準食材整合',{}).get('rows',[])}
        new_catalog={str(r.get('Food_ID')):r.get('合併標準食材名稱') for r in catalog}
        result['baseline_comparison']['catalog']={
            'old_rows':len(old_catalog),'new_rows':len(new_catalog),
            'added':{k:new_catalog[k] for k in new_catalog.keys()-old_catalog.keys()},
            'removed':{k:old_catalog[k] for k in old_catalog.keys()-new_catalog.keys()},
            'changed_names':{k:{'old':old_catalog[k],'new':new_catalog[k]} for k in old_catalog.keys()&new_catalog.keys()
                             if old_catalog[k]!=new_catalog[k]},
        }
    # Formula inspection does not execute or recalculate workbook content.
    formula_book=load_workbook(BytesIO(content),read_only=True,data_only=False)
    try:
        result['formula_counts']={s.title:sum(c.data_type=='f' for row in s.iter_rows() for c in row) for s in formula_book}
    finally:
        formula_book.close()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.workbook,args.baseline)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('sha256','sheets','contract_issues','orphan_references','recipe_unique_food_ids')},ensure_ascii=True))
