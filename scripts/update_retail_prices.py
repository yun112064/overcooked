"""Small PXMart source-to-JSON preview. No database writes, no scheduling.

Use only where access to the source is permitted. A single user-initiated
request may be made after --live is explicitly set. The generated JSON follows
scripts/import_retail_prices.py and must be reviewed before importing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Allow direct execution: python scripts/update_retail_prices.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.pricing_sources.common import SourceAccessBlocked


def to_import_row(record, observed_at: datetime) -> dict:
    if record.manual_review_reason:
        raise ValueError(f'需要人工審查：{record.manual_review_reason}')
    weight = Decimal(str(record.package_weight_g))
    price = Decimal(str(record.package_price))
    if not weight.is_finite() or weight <= 0 or not price.is_finite() or price <= 0:
        raise ValueError('價格或重量無效')
    if not record.source_record_id or not record.product_name or not record.source_url:
        raise ValueError('商品識別、名稱或來源網址缺失')
    return {
        'retailer': 'pxmart',
        'source_product_id': str(record.source_record_id),
        'product_name': record.product_name,
        'package_weight_g': str(weight),
        'package_price': str(price),
        'source_url': record.source_url,
        'region': record.region,
        'promotion_text': record.promotion_text,
        'source_method': record.source_method or 'pxmart_search',
        'observed_at': observed_at.isoformat(),
        # Conservative test expiry; actual official source validity is not verified.
        'valid_until': (observed_at + timedelta(hours=24)).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='全聯商品預覽；不寫資料庫、不啟動排程')
    ap.add_argument('--keyword', default='雞胸肉')
    ap.add_argument('--term', default='雞胸')
    ap.add_argument('--live', action='store_true', help='明確同意進行一次少量來源請求')
    ap.add_argument('--output', type=Path, default=Path('local_data/pxmart_preview.json'))
    args = ap.parse_args()
    if not args.live:
        print('未連線：需要 --live 才進行一次少量真實查詢；不寫入資料庫。')
        return 0
    try:
        from backend.app.pricing_sources import PXMart_api
        returned, records = PXMart_api.fetch_products(args.keyword, [args.term], page_size=3)
        observed = datetime.now(timezone.utc)
        accepted, rejected = [], []
        for record in records:
            try:
                accepted.append(to_import_row(record, observed))
            except ValueError as exc:
                rejected.append((record.product_name, str(exc)))
        print(f'來源回傳：{returned}；解析成功：{len(records)}；可預覽：{len(accepted)}；須人工審查：{len(rejected)}')
        for name, reason in rejected:
            print(f'跳過：{name}：{reason}')
        if not accepted:
            print('無可用商品，未產生 JSON。')
            return 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            print(f'檔案已存在，不覆蓋：{args.output}')
            return 1
        args.output.write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已產生僅供人工審閱的 JSON：{args.output}；未寫入資料庫。')
        return 0
    except SourceAccessBlocked as exc:
        print(f'來源拒絕存取，停止：{exc}', file=sys.stderr)
        return 2
    except Exception as exc:
        print(f'來源測試失敗：{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
