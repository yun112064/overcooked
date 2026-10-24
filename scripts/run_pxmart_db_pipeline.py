"""Step 12: import resumable PXBOX batch results into the TEST retail DB.

Step 11 owns network access and checkpoints. This step consumes that checkpoint,
excludes rejected records, and records a database update run. No ingredient
mappings are ever created; imported prices are unverified PXBOX listed prices.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_retail_prices import apply as import_apply, validate
from scripts.run_retail_test_batch import checked_test_url, record_start, record_finish


def prepare(state: dict) -> tuple[list[dict], dict]:
    if (state.get('version') != 1 or state.get('source') != 'pxbox_online'
            or not isinstance(state.get('runs'), dict)
            or not isinstance(state.get('products_by_ingredient'), dict)):
        raise ValueError('Step 11 進度檔格式不正確')
    # Only import completed searches; do not silently approve failed results.
    status = {key: entry.get('status') for key, entry in state['runs'].items()}
    gathered = []
    for ingredient_id, products in state['products_by_ingredient'].items():
        if status.get(ingredient_id) != 'ok' or not isinstance(products, list):
            continue
        for product in products:
            if product.get('review_status') == 'pending_manual_match' and not product.get('review_reason'):
                gathered.append(product)
    # Avoid duplicate products from overlapping ingredient searches.
    unique = {}
    for product in gathered:
        key = (product.get('retailer'), product.get('source_product_id'))
        if key[0] != 'pxmart' or not key[1]:
            continue
        if key in unique:
            old = unique[key]
            for col in ('package_price', 'package_weight_g', 'product_name'):
                if str(old.get(col)) != str(product.get(col)):
                    raise ValueError(f'同一商品 {key[1]} 的價格、規格或名稱有衝突，停止匯入')
        unique[key] = product
    stamp = state.get('updated_at_utc')
    if not stamp:
        raise ValueError('缺少查價完成時間')
    observed = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    if observed.tzinfo is None:
        raise ValueError('查價時間缺少時區')
    rows = []
    for product in unique.values():
        rows.append({
            'retailer': 'pxmart',
            'source_product_id': product['source_product_id'],
            'product_name': product['product_name'],
            'package_weight_g': product['package_weight_g'],
            'package_price': product['package_price'],
            'source_url': product.get('source_url'),
            'region': product.get('region'),
            'promotion_text': product.get('promotion_text'),
            'source_method': product.get('source_method') or 'pxmart_search',
            'observed_at': observed.isoformat(),
            'valid_until': (observed + timedelta(days=1)).isoformat(),
        })
    failed = sum(x == 'failed' for x in status.values())
    missing = sum(x == 'no_parsed_products' for x in status.values())
    return rows, {'searched': len(status), 'failed': failed, 'no_parsed': missing,
                  'excluded_or_unparsed': len(gathered) - len(unique),
                  'candidates': len(rows)}


def write_batch(rows: list[dict], summary: dict, url: str) -> tuple[int, str, int, int]:
    # Keep the existing importer API; URL checked again before creating a run.
    checked_test_url(url)
    run_id = record_start(url, 'pxmart', summary['searched'])
    inserted = skipped = 0
    try:
        if rows:
            # validate() reads JSON; use a temporary input under local_data only.
            # Apply already receives cleaned rows, so validate the candidate file
            # in main() and use its returned cleaned rows here.
            counts = import_apply(rows)
            inserted, skipped = int(counts['snapshots']), int(counts['skipped'])
        status = 'partial' if (summary['failed'] or summary['no_parsed']) else 'succeeded'
        reason = (f"搜尋失敗={summary['failed']}；無可解析商品={summary['no_parsed']}；"
                  '候選商品尚未人工確認食材配對及原價')
        record_finish(url, run_id, status, inserted, skipped, reason)
        return run_id, status, inserted, skipped
    except BaseException as exc:
        record_finish(url, run_id, 'failed', inserted, skipped, f'{type(exc).__name__}: {exc}'[:1400])
        raise


def main() -> int:
    p = argparse.ArgumentParser(description='Step 12：Step 11 查價進度 → 測試 PostgreSQL；不抓取網站')
    p.add_argument('--state', type=Path, default=Path('local_data/pxmart_catalog_batch_state.json'))
    p.add_argument('--out', type=Path, default=Path('local_data/pxmart_step12_import.json'))
    p.add_argument('--apply', action='store_true', help='僅寫入 meal_planner_retail_test')
    args = p.parse_args()
    try:
        state = json.loads(args.state.read_text(encoding='utf-8-sig'))
        rows, summary = prepare(state)
        print('批次摘要:', summary)
        if not rows:
            print('沒有合格測試候選商品；不建立價格快照，也不標記成功。')
            return 0 if not args.apply else 2
        # Write intermediate file, but do not overwrite an existing result.
        if not args.out.exists():
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open('x', encoding='utf-8') as file:
                json.dump(rows, file, ensure_ascii=False, indent=2)
        else:
            prior = json.loads(args.out.read_text(encoding='utf-8-sig'))
            if prior != rows:
                raise ValueError('輸出檔已存在且內容不同；請指定新的 --out 路徑')
        cleaned = validate(args.out)
        print(f'預檢通過 {len(cleaned)} 筆；排除 rejected；不建立 ingredient_id 配對。')
        if not args.apply:
            print('預覽完成，未連線資料庫。')
            return 0
        url = checked_test_url(os.environ.get('DATABASE_URL'))
        run_id, status, inserted, skipped = write_batch(cleaned, summary, url)
        print(f'run_id={run_id} status={status} 新增快照={inserted} 略過={skipped}')
        return 0
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f'已停止：{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
