"""把開發資料庫中整理好的資料匯出成 data/seed/*.json（資料種子），讓隊友 clone 後以一個指令補齊。

用法（唯讀；讀 DATABASE_URL 與 KNOWLEDGE_DATABASE_URL）：
  .\\scripts\\dev-run.ps1 scripts\\export_seed_data.py [輸出目錄，預設 data/seed]

只匯出白名單資料表（以下 SQL 是唯一的讀取點，刻意不 import app，避免與其他工作目錄的程式版本不一致）：
  - conversion_factor_estimates（烹調推估、單位克數、TFDA 對照含人工對照、空白營養推估；全部列與欄位）
  - knowledge_records 現行版本中 status=approved 且 classification=nutrition 者（不含 seq 與向量）
  - source_articles：上述知識對應的文章中繼資料
  - price_collection_runs／price_quote_snapshots（全聯、萬家福價格快照）
不讀取任何家庭、成員、需求、生成結果、採用紀錄、工作佇列、額度或帳號等個人／使用紀錄資料表。
匯入：python -m app.jobs.import_seed_data（見 README「首次啟動匯入資料種子」）。
"""
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "seed"

APP_QUERIES = {
    "conversion_factor_estimates.json": (
        "SELECT * FROM conversion_factor_estimates ORDER BY kind, food_id, factor_key, created_at, id"),
    "price_collection_runs.json": "SELECT * FROM price_collection_runs ORDER BY run_key",
    # run_id 換成 run_key（自然鍵），匯入時對到目標庫的 run；id 為流水號不匯出
    "price_quote_snapshots.json": (
        "SELECT r.run_key, s.provider, s.canonical_name, s.status, s.missing_reason, s.queried_at, s.valid_from, "
        "s.valid_until, s.product_id, s.product_name, s.price, s.currency, s.package_quantity, s.package_unit, "
        "s.source_url, s.quote_payload FROM price_quote_snapshots s "
        "JOIN price_collection_runs r ON r.id = s.run_id ORDER BY r.run_key, s.canonical_name"),
}
_CURRENT_APPROVED = (
    "SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records ORDER BY knowledge_id, seq DESC) c "
    "WHERE c.status = 'approved' AND c.classification = 'nutrition'")
KNOWLEDGE_QUERIES = {
    "knowledge_records.json": f"SELECT * FROM ({_CURRENT_APPROVED}) k ORDER BY knowledge_id, source_version",
    "knowledge_source_articles.json": (
        f"SELECT a.* FROM source_articles a WHERE EXISTS (SELECT 1 FROM ({_CURRENT_APPROVED}) k "
        "WHERE k.source_name = a.source_name AND k.article_id = a.article_id) ORDER BY a.source_name, a.article_id"),
}
DROP_COLUMNS = {"knowledge_records.json": {"seq"}}

SOURCES = {
    "conversion_factor_estimates.json": "烹調產率／保留率、單位克數、TFDA 樣本對照（含人工對照）、TFDA 空白營養推估"
                                        "（LLM 推估與人工值皆保留 status／source／reviewer 等審核欄位）",
    "knowledge_records.json": "健康醫療網營養知識：現行版本、已核准、分類為營養知識；不含向量，匯入後需重建索引",
    "knowledge_source_articles.json": "上述知識對應的健康醫療網文章中繼資料（不含原文全文）",
    "price_collection_runs.json": "全聯（pxmart）與萬家福（uniprosperity）價格蒐集批次",
    "price_quote_snapshots.json": "價格快照；以 run_key＋canonical_name 對應批次",
}
LICENSE_NOTES = [
    "健康醫療網（healthnews.com.tw）內容：經電話口頭授權使用，顯示時必須帶出處（決策 #25）。",
    "萬家福價格：網站條款風險由使用者承擔（決策 #26）。",
    "不含任何家庭、成員、需求、生成結果、採用紀錄、工作佇列、額度或帳號資料。",
]


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"無法序列化 {type(value).__name__}")


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _fetch(url: str, queries: dict) -> dict:
    with psycopg.connect(_libpq(url), row_factory=dict_row) as conn:
        conn.read_only = True  # 交易層級唯讀：任何寫入都會被資料庫拒絕
        out = {name: conn.execute(sql).fetchall() for name, sql in queries.items()}
        version = None
        if "conversion_factor_estimates.json" in queries:
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
    for name, rows in out.items():
        for row in rows:
            for column in DROP_COLUMNS.get(name, ()):
                row.pop(column, None)
    return out, version


def _write(path: Path, rows) -> bytes:
    data = (json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True, default=_json_value) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


def export(app_url: str, knowledge_url: str, out_dir) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    app_rows, alembic_version = _fetch(app_url, APP_QUERIES)
    knowledge_rows, _ = _fetch(knowledge_url, KNOWLEDGE_QUERIES)
    files = {}
    for name, rows in sorted({**app_rows, **knowledge_rows}.items()):
        data = _write(out_dir / name, rows)
        files[name] = {"count": len(rows), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "database": "knowledge" if name in KNOWLEDGE_QUERIES else "app", "source": SOURCES[name]}
    manifest = {"format_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "alembic_version": alembic_version, "files": files, "license_notes": LICENSE_NOTES,
                "import_command": "python -m app.jobs.import_seed_data",
                "after_import": "python -m knowledge_rag.admin index build --version <索引版本>"}
    _write(out_dir / "manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out_dir = Path(argv[0]) if argv else DEFAULT_OUT
    app_url, knowledge_url = os.environ.get("DATABASE_URL"), os.environ.get("KNOWLEDGE_DATABASE_URL")
    if not app_url or not knowledge_url:
        print("錯誤：需要 DATABASE_URL 與 KNOWLEDGE_DATABASE_URL（以 scripts/dev-run.ps1 執行）", file=sys.stderr)
        return 1
    manifest = export(app_url, knowledge_url, out_dir)
    for name, info in manifest["files"].items():
        print(f"{name}: {info['count']} 筆，{info['bytes']:,} bytes")
    print(f"alembic {manifest['alembic_version']} → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
