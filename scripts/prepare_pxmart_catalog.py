"""Prepare a stable 434-ingredient PXMart query manifest from existing recipe tables.

Read-only database operation. Output is local_data/*.json, not a priced product feed.
This program NEVER runs the crawler or changes price/recipe tables.
"""
from __future__ import annotations
import argparse
import json
import os
from collections import defaultdict
from pathlib import Path


def build_catalog(ingredients, aliases):
    names = defaultdict(set)
    for iid, name in ingredients:
        if iid and name and str(name).strip():
            names[str(iid)].add(str(name).strip())
    alias_by_id = defaultdict(set)
    for iid, alias in aliases:
        if iid and alias and str(iid) in names:
            alias_by_id[str(iid)].add(str(alias).strip())
    result = []
    for iid in sorted(names):
        # Multiple names per ID require review, not arbitrary selection.
        if len(names[iid]) != 1:
            raise ValueError(f'{iid} 的 canonical_name 不唯一：{sorted(names[iid])}')
        canonical = next(iter(names[iid]))
        result.append({
            'ingredient_id': iid,
            'canonical_name': canonical,
            'search_keyword': canonical,
            'aliases': sorted(a for a in alias_by_id[iid] if a and a != canonical),
            'enabled': False,  # Require review before any automated external requests.
            'match_status': 'pending',
        })
    return result


def main():
    p = argparse.ArgumentParser(description='輸出食材查價清單；不抓取網站、不修改資料庫')
    p.add_argument('--out', type=Path, default=Path('local_data/pxmart_ingredient_catalog.json'))
    args = p.parse_args()
    if args.out.exists():
        p.error(f'檔案已存在，拒絕覆蓋：{args.out}')
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        p.error('需先設定 DATABASE_URL；不會讀取或顯示密碼')
    from sqlalchemy import create_engine, text
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ingredients = conn.execute(text('''
                SELECT DISTINCT ingredient_id, canonical_name
                FROM recipe_ingredients WHERE ingredient_id IS NOT NULL
            ''')).all()
            aliases = conn.execute(text('''
                SELECT ingredient_id, alias FROM recipe_ingredient_aliases
            ''')).all()
    finally:
        engine.dispose()
    catalog = build_catalog(ingredients, aliases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已輸出 {len(catalog)} 種食材：{args.out}')
    print('所有食材預設 enabled=false；沒有連線商店、沒有修改資料庫。')

if __name__ == '__main__':
    main()
