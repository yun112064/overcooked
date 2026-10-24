"""Convert PXBOX batch preview into importer-compatible JSON.

This does not verify ingredient matches, validate original retail prices, or access a database.
Only explicit pending_manual_match candidates are exported for *test-only* import.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def transform(payload: dict, *, ttl_hours: int = 24) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get('products'), list):
        raise ValueError('輸入必須是包含 products 陣列的預覽 JSON')
    generated = datetime.fromisoformat(str(payload['generated_at_utc']).replace('Z', '+00:00'))
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError('generated_at_utc 必須包含時區')
    if ttl_hours < 1 or ttl_hours > 72:
        raise ValueError('ttl-hours 必須為 1..72')
    observed = generated.astimezone(timezone.utc)
    valid = observed + timedelta(hours=ttl_hours)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in payload['products']:
        if not isinstance(row, dict) or row.get('review_status') != 'pending_manual_match':
            continue
        if row.get('price_channel') != 'pxbox_online' or row.get('retailer') != 'pxmart':
            continue
        if row.get('price_type') != 'online_listed_unverified_regular':
            continue
        source_id = str(row.get('source_product_id') or '').strip()
        if not source_id:
            continue
        key = ('pxmart', source_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'retailer': 'pxmart',
            'source_product_id': source_id,
            'product_name': row['product_name'],
            'package_weight_g': row['package_weight_g'],
            'package_price': row['package_price'],
            'source_url': row.get('source_url'),
            'region': row.get('region'),
            'promotion_text': row.get('promotion_text'),
            'source_method': row.get('source_method') or 'pxmart_search',
            'observed_at': observed.isoformat(),
            'valid_until': valid.isoformat(),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description='將 PXBOX 預覽轉成測試用價格匯入 JSON（不寫入資料庫）')
    parser.add_argument('--preview', type=Path, default=Path('local_data/pxmart_batch_preview.json'))
    parser.add_argument('--out', type=Path, default=Path('local_data/pxmart_test_import.json'))
    parser.add_argument('--ttl-hours', type=int, default=24)
    args = parser.parse_args()
    try:
        payload = json.loads(args.preview.read_text(encoding='utf-8-sig'))
        rows = transform(payload, ttl_hours=args.ttl_hours)
        if not rows:
            print('沒有符合待人工配對條件的候選商品；未建立匯入檔案。')
            return 0
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x', encoding='utf-8') as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        print(f'產生 {len(rows)} 筆測試候選商品：{args.out}；未寫入資料庫。')
        print('注意：這不是原價驗證，也不是 ingredient_id 正式配對。')
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f'已停止：{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
