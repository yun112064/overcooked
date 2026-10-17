"""Low-volume PXBOX query preview for enabled ingredient manifest entries.

No database writes, no automatic ingredient mapping, no scheduler.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.pricing_sources import PXMart_api
from backend.app.pricing_sources.common import SourceAccessBlocked

CHICKEN = '雞胸肉'
CHICKEN_BLOCK = ('紐奧良', '黑胡椒', '醃', '調味', '即食', '舒肥', '煙燻', '熟食', '炸', '烤', '雞排', '料理包', '沙拉', '香草', '椒麻', '蒜香', '咖哩', '蜜汁', '香煎')
EGG = '雞蛋'
EGG_BLOCK = ('蛋糕', '蛋捲', '布丁', '茶葉蛋', '滷蛋', '鹹蛋', '皮蛋', '蛋餅', '蛋黃酥', '蛋白粉', '蛋液')
RICE = '白米'
RICE_BLOCK = ('糙米', '紫米', '黑米', '糯米', '米餅', '米粉', '米麩', '米漿', '飯糰', '即食飯', '米菓', '爆米')


def screen(ingredient: dict, record) -> str | None:
    name = record.product_name
    canonical = ingredient['canonical_name']
    if record.manual_review_reason:
        return 'parser_manual_review'
    if record.package_weight_g <= 0 or record.package_price <= 0:
        return 'invalid_weight_or_price'
    if canonical == CHICKEN:
        if '雞胸' not in name and '胸肉' not in name:
            return 'ingredient_mismatch'
        if any(token in name for token in CHICKEN_BLOCK):
            return 'seasoned_or_processed_chicken'
    elif canonical == EGG:
        if not any(token in name for token in ('雞蛋', '鮮蛋', '洗選蛋')):
            return 'ingredient_mismatch'
        if any(token in name for token in EGG_BLOCK):
            return 'processed_egg'
    elif canonical == RICE:
        if not any(token in name for token in ('白米', '蓬萊米', '稉米', '粳米')):
            return 'ingredient_mismatch'
        if any(token in name for token in RICE_BLOCK):
            return 'other_rice_or_processed'
    return None


def preview_records(ingredient: dict, records) -> list[dict]:
    out = []
    for record in records:
        reason = screen(ingredient, record)
        out.append({
            'ingredient_id_candidate': ingredient['ingredient_id'],
            'canonical_name': ingredient['canonical_name'],
            'retailer': 'pxmart',
            'price_channel': 'pxbox_online',
            'price_type': 'online_listed_unverified_regular',
            'source_product_id': record.source_record_id,
            'product_name': record.product_name,
            'package_weight_g': str(record.package_weight_g),
            'package_price': str(record.package_price),
            'source_url': record.source_url,
            'promotion_text': record.promotion_text,
            'region': record.region,
            'source_method': record.source_method or 'pxmart_search',
            'review_status': 'rejected' if reason else 'pending_manual_match',
            'review_reason': reason,
        })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description='小批次 PXBOX 搜尋預覽；不寫入資料庫')
    p.add_argument('--catalog', type=Path, default=Path('local_data/pxmart_ingredient_catalog.json'))
    p.add_argument('--out', type=Path, default=Path('local_data/pxmart_batch_preview.json'))
    p.add_argument('--page-size', type=int, default=5)
    p.add_argument('--max-ingredients', type=int, default=3)
    p.add_argument('--interval-seconds', type=float, default=3)
    p.add_argument('--live', action='store_true')
    args = p.parse_args()
    if not 1 <= args.page_size <= 5 or not 1 <= args.max_ingredients <= 3 or args.interval_seconds < 3:
        p.error('安全限制：page-size 1..5、max-ingredients 1..3、interval-seconds >=3')
    if not args.catalog.is_file():
        p.error(f'找不到清單：{args.catalog}')
    catalog = json.loads(args.catalog.read_text(encoding='utf-8-sig'))
    if not isinstance(catalog, list):
        p.error('catalog 必須是 JSON 陣列')
    enabled = [x for x in catalog if x.get('enabled') is True]
    if len(enabled) > args.max_ingredients:
        p.error(f'已啟用 {len(enabled)} 種，超過本次上限 {args.max_ingredients}，請勿一次批次查詢')
    print('已啟用食材：', [(x['ingredient_id'], x['canonical_name']) for x in enabled])
    if not args.live:
        print('僅檢查清單；加上 --live 才會連線來源，不會寫入資料庫。')
        return 0
    if not enabled:
        print('沒有已啟用的食材，未連線。')
        return 0
    if args.out.exists():
        p.error(f'輸出檔已存在，不覆蓋：{args.out}')
    result = []
    runlog = []
    for idx, ingredient in enumerate(enabled):
        if idx:
            time.sleep(args.interval_seconds)
        keyword = str(ingredient.get('search_keyword') or ingredient['canonical_name']).strip()
        # Avoid aliases to keep each request narrowly tied to the canonical ingredient.
        try:
            fetched, records = PXMart_api.fetch_products(keyword, [keyword], page_size=args.page_size)
        except SourceAccessBlocked as exc:
            print(f'來源拒絕存取，立即停止：{exc}')
            return 2  # No output on access block; never bypass.
        except Exception as exc:
            print(f'{keyword} 查詢失敗：{type(exc).__name__}: {exc}')
            runlog.append({'ingredient_id': ingredient['ingredient_id'], 'status': 'failed', 'error_type': type(exc).__name__})
            continue
        converted = preview_records(ingredient, records)
        result.extend(converted)
        runlog.append({'ingredient_id': ingredient['ingredient_id'], 'status': 'ok', 'source_returned': fetched, 'parsed': len(records), 'pending_review': sum(x['review_status'] == 'pending_manual_match' for x in converted)})
        print(f'{keyword}：來源 {fetched}、解析 {len(records)}、待人工配對 {runlog[-1]["pending_review"]}')
    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'purpose': 'review_only_no_db_write',
        'price_notice': 'PXBOX 線上顯示售價，無法證實是實體大全聯原價；即使未標記促銷亦不得稱為原價',
        'runs': runlog,
        'products': result,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'已輸出 {args.out}；商品 {len(result)} 筆；未寫入 PostgreSQL。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
