"""Safely import a prepared retail JSON batch and record its execution.

Test database ONLY. Does not crawl, schedule, or automatically map ingredients.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_retail_prices import apply, validate  # noqa: E402


def checked_test_url(raw: str | None) -> str:
    if not raw:
        raise ValueError('請先明確設定 DATABASE_URL，且只允許測試資料庫')
    from sqlalchemy.engine import make_url
    url = make_url(raw)
    if url.get_backend_name() != 'postgresql' or url.database != 'meal_planner_retail_test':
        raise ValueError('安全限制：只允許 PostgreSQL meal_planner_retail_test，不可寫入正式資料庫')
    return raw


def record_start(url: str, retailer: str, fetched: int) -> int:
    from sqlalchemy import create_engine, text
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            return conn.execute(text('''
                INSERT INTO retail_price_update_runs (retailer, status, fetched_count)
                VALUES (:retailer, 'running', :fetched)
                RETURNING run_id
            '''), {'retailer': retailer, 'fetched': fetched}).scalar_one()
    finally:
        engine.dispose()


def record_finish(url: str, run_id: int, status: str, inserted: int, skipped: int, reason: str | None) -> None:
    from sqlalchemy import create_engine, text
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text('''
                UPDATE retail_price_update_runs
                   SET finished_at=:finished, status=:status,
                       inserted_count=:inserted, skipped_count=:skipped,
                       failure_reason=:reason
                 WHERE run_id=:run_id
            '''), {
                'run_id': run_id, 'finished': datetime.now(timezone.utc),
                'status': status, 'inserted': inserted,
                'skipped': skipped, 'reason': reason,
            })
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description='既有零售 JSON 測試匯入 + 執行紀錄，不執行爬蟲')
    parser.add_argument('--file', type=Path, default=Path('local_data/pxmart_test_import.json'))
    parser.add_argument('--apply', action='store_true', help='只寫入 meal_planner_retail_test')
    args = parser.parse_args()
    try:
        rows = validate(args.file)
        retailers = {r['retailer'] for r in rows}
        if retailers != {'pxmart'}:
            raise ValueError('這個測試批次目前僅支援 pxmart 單一通路')
        print(f'預檢通過：{len(rows)} 筆；不會建立 ingredient_id 對應')
        if not args.apply:
            print('預覽模式：未連線資料庫、未新增更新紀錄')
            return 0
        url = checked_test_url(os.environ.get('DATABASE_URL'))
        run_id = record_start(url, 'pxmart', len(rows))
        print(f'測試更新紀錄 run_id={run_id} 已建立')
        try:
            counts = apply(rows)
        except Exception as exc:
            record_finish(url, run_id, 'failed', 0, 0, f'{type(exc).__name__}: {exc}'[:1500])
            print(f'批次失敗，已記錄：{type(exc).__name__}: {exc}', file=sys.stderr)
            return 1
        inserted = int(counts['snapshots'])
        skipped = int(counts['skipped'])
        record_finish(url, run_id, 'succeeded', inserted, skipped, None)
        print(f'完成：run_id={run_id}；新增快照={inserted}；略過重複={skipped}')
        return 0
    except Exception as exc:
        print(f'已停止：{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

