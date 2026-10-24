"""Import reviewed retail JSON records into existing four-table schema.

Default is offline validation. --apply uses DATABASE_URL and one transaction.
No crawler calls; no automatic ingredient matches; no recipe table writes.

Format: JSON list of objects with retailer, source_product_id, product_name,
package_weight_g, package_price, observed_at (ISO offset-aware), source_method.
Optional: package_description, source_url, region, promotion_text, valid_until.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

RETAILERS = {'pxmart', 'carrefour'}


def decimal_number(value, label, positive=False):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{label} 必須是數字') from None
    if not d.is_finite() or (d <= 0 if positive else d < 0):
        raise ValueError(f'{label} 必須是有效的{"正數" if positive else "非負數"}')
    return d


def timestamp(value, label):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{label} 必須是 ISO 8601 時間') from None
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError(f'{label} 必須含時區（例如 +08:00）')
    return d


def validate(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(raw, list) or not raw:
        raise ValueError('JSON 必須為非空陣列')
    cleaned, seen = [], set()
    allowed = {'retailer', 'source_product_id', 'product_name', 'package_weight_g', 'package_price',
               'observed_at', 'source_method', 'package_description', 'source_url', 'region',
               'promotion_text', 'valid_until'}
    for no, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f'第 {no} 筆必須是物件')
        unexpected = set(item) - allowed
        if unexpected:
            raise ValueError(f'第 {no} 筆未知欄位：{sorted(unexpected)}')
        retailer = str(item.get('retailer') or '').strip().lower()
        if retailer not in RETAILERS:
            raise ValueError(f'第 {no} 筆 retailer 僅可為 pxmart/carrefour')
        product_id = str(item.get('source_product_id') or '').strip()
        name = str(item.get('product_name') or '').strip()
        method = str(item.get('source_method') or '').strip()
        if not product_id or len(product_id) > 150 or not name or not method or len(method) > 30:
            raise ValueError(f'第 {no} 筆缺少合法的商品 ID／名稱／來源方法')
        weight = decimal_number(item.get('package_weight_g'), f'第 {no} 筆包裝重量', positive=True)
        price = decimal_number(item.get('package_price'), f'第 {no} 筆價格')
        if price.as_tuple().exponent < -2 or weight.as_tuple().exponent < -6:
            raise ValueError(f'第 {no} 筆價格最多 2 位小數、重量最多 6 位小數')
        if price >= Decimal('1e16') or weight >= Decimal('1e12'):
            raise ValueError(f'第 {no} 筆數值超出資料庫容量')
        observed = timestamp(item.get('observed_at'), f'第 {no} 筆 observed_at')
        valid = timestamp(item['valid_until'], f'第 {no} 筆 valid_until') if item.get('valid_until') else None
        if valid is not None and valid <= observed:
            raise ValueError(f'第 {no} 筆 valid_until 必須晚於 observed_at')
        key = (retailer, product_id, observed.isoformat(), str(item.get('region') or ''))
        if key in seen:
            raise ValueError(f'第 {no} 筆來源商品／時間／地區重複')
        seen.add(key)
        cleaned.append({
            'retailer': retailer, 'source_product_id': product_id, 'product_name': name,
            'package_weight_g': weight, 'package_price': price,
            'package_description': item.get('package_description'), 'source_url': item.get('source_url'),
            'region': item.get('region'), 'promotion_text': item.get('promotion_text'),
            'observed_at': observed, 'valid_until': valid, 'source_method': method,
        })
    return cleaned


def apply(rows):
    from sqlalchemy import create_engine, text
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError('未設定 DATABASE_URL；--apply 需要明確指定本機連線，不使用隱藏預設值')
    engine = create_engine(db_url)
    counts = defaultdict(int)
    # All writes are rolled back if anything fails. For a pre-populated database,
    # repeated snapshots from the same observation time are skipped.
    with engine.begin() as conn:
        for row in rows:
            p = conn.execute(text('''
                INSERT INTO retail_products
                    (retailer, source_product_id, product_name, package_weight_g,
                     package_description, source_url)
                VALUES (:retailer, :source_product_id, :product_name, :package_weight_g,
                        :package_description, :source_url)
                ON CONFLICT (retailer, source_product_id) DO UPDATE SET
                    product_name = EXCLUDED.product_name,
                    package_weight_g = EXCLUDED.package_weight_g,
                    package_description = EXCLUDED.package_description,
                    source_url = EXCLUDED.source_url,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING product_id
            '''), row).scalar_one()
            existing = conn.execute(text('''
                SELECT snapshot_id FROM retail_price_snapshots
                WHERE product_id=:product_id AND observed_at=:observed_at
                  AND region IS NOT DISTINCT FROM :region
                LIMIT 1
            '''), {'product_id':p, 'observed_at':row['observed_at'], 'region':row['region']}).scalar_one_or_none()
            if existing is not None:
                counts['skipped'] += 1
                continue
            conn.execute(text('''
                INSERT INTO retail_price_snapshots
                    (product_id, package_price, currency, observed_at, valid_until,
                     region, promotion_text, source_method)
                VALUES (:product_id, :package_price, 'TWD', :observed_at, :valid_until,
                        :region, :promotion_text, :source_method)
            '''), {**row, 'product_id':p})
            counts['snapshots'] += 1
    engine.dispose()
    return counts


def main():
    parser = argparse.ArgumentParser(description='零售價格 JSON 預檢／手動匯入')
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='確認後才寫入本機資料庫')
    args = parser.parse_args()
    try:
        rows = validate(args.file)
        print(f'預檢通過：{len(rows)} 筆；通路：{sorted({r["retailer"] for r in rows})}')
        print('不會自動匹配 ingredient_id，也不修改食譜表。')
        if not args.apply:
            print('僅預檢，未寫入資料庫。')
            return 0
        counts = apply(rows)
        print(f'交易已提交：新增快照 {counts["snapshots"]} 筆，略過重複 {counts["skipped"]} 筆。')
        return 0
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f'已停止：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
