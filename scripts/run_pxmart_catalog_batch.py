"""Resumable, rate-limited PXBOX catalog preview. No database writes.

The existing preview_records() controls product review status. This script does
not create verified ingredient mappings or claim that online prices are regular
in-store prices.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.pricing_sources import PXMart_api
from backend.app.pricing_sources.common import SourceAccessBlocked
from scripts.batch_pxmart_preview import preview_records

NOTICE = 'PXBOX 線上標示售價；非經證實的全台大全聯實體原價。商品匹配皆須審核。'


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(temp, path)


def load_catalog(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(raw, list):
        raise ValueError('食材清單必須是 JSON 陣列')
    selected, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError('食材清單包含非物件資料')
        if row.get('enabled') is not True:
            continue
        key = str(row.get('ingredient_id', '')).strip()
        name = str(row.get('canonical_name', '')).strip()
        if not key or not name or key in seen:
            raise ValueError('啟用食材 ID 或名稱缺失／重複')
        seen.add(key)
        selected.append(row)
    return selected


def initial_state() -> dict:
    return {'version': 1, 'source': 'pxbox_online', 'purpose': 'review_only_no_db_write',
            'price_notice': NOTICE, 'created_at_utc': utcnow(),
            'updated_at_utc': utcnow(), 'runs': {}, 'products_by_ingredient': {}}


def run_batch(selected: list[dict], state: dict, *, page_size: int,
              interval_seconds: float, fetcher=None, sleeper=None) -> dict:
    fetcher = fetcher or PXMart_api.fetch_products
    sleeper = sleeper or time.sleep
    attempted = 0
    for item in selected:
        key = item['ingredient_id']
        if key in state['runs']:
            continue
        if attempted:
            sleeper(interval_seconds)
        attempted += 1
        keyword = str(item.get('search_keyword') or item['canonical_name']).strip()
        try:
            fetched, records = fetcher(keyword, [keyword], page_size=page_size)
            items = preview_records(item, records)
            state['products_by_ingredient'][key] = items
            state['runs'][key] = {'ingredient_name': item['canonical_name'],
                                  'status': 'ok' if records else 'no_parsed_products',
                                  'source_returned': fetched, 'parsed': len(records),
                                  'pending_review': sum(r['review_status'] == 'pending_manual_match' for r in items)}
        except SourceAccessBlocked:
            raise
        except Exception as exc:
            state['runs'][key] = {'ingredient_name': item['canonical_name'],
                                  'status': 'failed', 'error_type': type(exc).__name__}
        state['updated_at_utc'] = utcnow()
        yield key, state['runs'][key]['status']


def main() -> int:
    p = argparse.ArgumentParser(description='可續跑的 PXBOX 批次查價預覽；不寫入資料庫')
    p.add_argument('--catalog', type=Path, default=Path('local_data/pxmart_ingredient_catalog.json'))
    p.add_argument('--state', type=Path, default=Path('local_data/pxmart_catalog_batch_state.json'))
    p.add_argument('--page-size', type=int, default=5)
    p.add_argument('--max-ingredients', type=int, default=3, help='本次允許的已啟用食材總數（最多 434）')
    p.add_argument('--interval-seconds', type=float, default=3.0)
    p.add_argument('--live', action='store_true')
    args = p.parse_args()
    if not 1 <= args.page_size <= 5 or not 1 <= args.max_ingredients <= 434 or args.interval_seconds < 3:
        p.error('安全限制：page-size 1..5；max-ingredients 1..434；interval-seconds >=3')
    try:
        selected = load_catalog(args.catalog)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        p.error(str(exc))
    if len(selected) > args.max_ingredients:
        p.error(f'已啟用 {len(selected)} 種，超過 --max-ingredients={args.max_ingredients}；未連線')
    print(f'已啟用 {len(selected)} 種，最高查詢數 {len(selected)}；間隔至少 {args.interval_seconds:g} 秒。')
    if not args.live:
        print('預檢模式：未連線網站、未寫入資料庫。')
        return 0
    if not selected:
        return 0
    if args.state.exists():
        try:
            state = json.loads(args.state.read_text(encoding='utf-8-sig'))
            if (state.get('version') != 1 or state.get('source') != 'pxbox_online'
                    or not isinstance(state.get('runs'), dict)
                    or not isinstance(state.get('products_by_ingredient'), dict)):
                raise ValueError('進度檔格式不正確')
            previously = set(state['runs'])
            current = {r['ingredient_id'] for r in selected}
            if not previously.issubset(current):
                raise ValueError('進度檔含有目前未啟用的食材，請先另存進度檔，不可混用')
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            p.error(f'無法恢復進度：{exc}')
    else:
        state = initial_state()
    try:
        for key, status in run_batch(selected, state, page_size=args.page_size,
                                     interval_seconds=args.interval_seconds):
            atomic_json(args.state, state)
            print(f'{key}: {status}；進度 {len(state["runs"])}/{len(selected)}')
    except SourceAccessBlocked as exc:
        atomic_json(args.state, state)
        print(f'來源限制存取，已停止且不繞過：{exc}', file=sys.stderr)
        return 2
    total = sum(len(x) for x in state['products_by_ingredient'].values())
    print(f'完成／已續跑：{len(state["runs"])}/{len(selected)} 種；候選商品 {total} 筆。進度檔：{args.state}；未寫入 PostgreSQL。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
