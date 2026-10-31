"""匯入資料種子 data/seed/*.json（由 scripts/export_seed_data.py 從開發資料庫匯出）。

python -m app.jobs.import_seed_data [--seed-dir data/seed] [--dry-run] [--only app|knowledge]

  - 業務庫（DATABASE_URL）：conversion_factor_estimates、price_collection_runs、price_quote_snapshots
  - 知識庫（KNOWLEDGE_DATABASE_URL；未設定則略過）：knowledge_records、source_articles（先套用 knowledge_rag/schema.sql）

冪等：推估以 id、價格批次以 run_key、快照以 run_key＋canonical_name、知識以 (knowledge_id, source_version)、
文章以 (source_name, article_id) 去重；已存在的列一律保留不覆寫（本機的人工修改優先）。
匯入前檢查：種子檔的 sha256 與 manifest 一致；目標業務庫的 alembic 版本等於或晚於種子匯出時的版本。
--dry-run 在同一交易內執行後回滾，回報的筆數與實際匯入相同。
知識不含向量：匯入後需執行 python -m knowledge_rag.admin index build --version <索引版本>（付費向量化）。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_DIR = ROOT / "data" / "seed"
KNOWLEDGE_SCHEMA = ROOT / "knowledge_rag" / "schema.sql"
INDEX_HINT = ("知識已匯入但尚未建索引（種子不含向量）：請先啟動後端，再執行 "
              ".\\scripts\\dev-run.ps1 -m knowledge_rag.admin index build --version seed-<日期>"
              "（會經雲端額度呼叫付費向量化，約 200 筆）")


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def load_seed(seed_dir) -> tuple[dict, dict]:
    seed_dir = Path(seed_dir)
    manifest_path = seed_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"找不到 {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = {}
    for name, info in manifest["files"].items():
        raw = (seed_dir / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != info["sha256"]:
            raise SystemExit(f"{name} 與 manifest 的 sha256 不符，請重新取得種子檔")
        data[name] = json.loads(raw.decode("utf-8"))
    return manifest, data


def _ancestors(revision: str) -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    return {rev.revision for rev in script.iterate_revisions(revision, "base")}


def check_alembic(conn, seed_version: str) -> str:
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone() if conn.execute(
        "SELECT to_regclass('alembic_version')").fetchone()[0] else None
    if row is None:
        raise SystemExit("目標業務庫尚未建立資料表：請先 alembic upgrade head（或啟動 start-dev.ps1）")
    target = row[0]
    try:
        compatible = target == seed_version or seed_version in _ancestors(target)
    except Exception:  # 目標版本不在本機遷移鏈（程式比資料庫舊）
        compatible = False
    if not compatible:
        raise SystemExit(f"alembic 版本不相容：資料庫為 {target}，種子匯出時為 {seed_version}；"
                         "請先更新程式並 alembic upgrade head，或以相同版本重新匯出種子")
    return target


def _column_types(conn, table: str) -> dict:
    return dict(conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = %s", (table,)).fetchall())


def _insert(conn, table: str, rows: list[dict], conflict: str, *, extra: dict | None = None) -> dict:
    """逐列 INSERT … ON CONFLICT DO NOTHING；回傳新增／略過筆數。"""
    types = _column_types(conn, table)
    inserted = 0
    for row in rows:
        row = {**row, **(extra(row) if extra else {})}
        unknown = set(row) - set(types)
        if unknown:
            raise SystemExit(f"{table} 缺少欄位 {sorted(unknown)}：請先 alembic upgrade head")
        columns = sorted(row)
        values = [Jsonb(row[c]) if types[c] == "jsonb" and row[c] is not None else row[c] for c in columns]
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT {} DO NOTHING RETURNING 1").format(
            sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)), sql.SQL(conflict))
        inserted += conn.execute(statement, values).fetchone() is not None
    return {"inserted": inserted, "skipped": len(rows) - inserted}


def import_app(conn, manifest: dict, data: dict) -> dict:
    check_alembic(conn, manifest["alembic_version"])
    report = {"conversion_factor_estimates": _insert(
        conn, "conversion_factor_estimates", data["conversion_factor_estimates.json"], "(id)")}
    runs = data["price_collection_runs.json"]
    # 價格批次以 run_key 去重：本機已有同 run_key（即使 id 不同）就沿用本機那一列
    report["price_collection_runs"] = _insert(conn, "price_collection_runs", runs, "")
    run_ids = dict(conn.execute("SELECT run_key, id FROM price_collection_runs WHERE run_key = ANY(%s)",
                                ([r["run_key"] for r in runs],)).fetchall())
    snapshots = [{k: v for k, v in s.items() if k != "run_key"} | {"run_id": run_ids[s["run_key"]]}
                 for s in data["price_quote_snapshots.json"]]
    report["price_quote_snapshots"] = _insert(conn, "price_quote_snapshots", snapshots, "(run_id, canonical_name)")
    return report


def import_knowledge(conn, data: dict) -> dict:
    if KNOWLEDGE_SCHEMA.exists():
        conn.execute(KNOWLEDGE_SCHEMA.read_text(encoding="utf-8"))
    return {
        "knowledge_records": _insert(conn, "knowledge_records", data["knowledge_records.json"],
                                     "(knowledge_id, source_version)"),
        "source_articles": _insert(conn, "source_articles", data["knowledge_source_articles.json"],
                                   "(source_name, article_id)"),
    }


def run(seed_dir=DEFAULT_SEED_DIR, *, app_url: str | None, knowledge_url: str | None, dry_run: bool = False,
        only: str | None = None) -> dict:
    manifest, data = load_seed(seed_dir)
    report = {"dry_run": dry_run, "seed_exported_at": manifest["exported_at"]}
    targets = []
    if only in (None, "app"):
        if not app_url:
            raise SystemExit("需要 DATABASE_URL")
        targets.append((app_url, lambda c: import_app(c, manifest, data)))
    if only in (None, "knowledge"):
        if knowledge_url:
            targets.append((knowledge_url, lambda c: import_knowledge(c, data)))
        else:
            report["knowledge"] = "skipped：未設定 KNOWLEDGE_DATABASE_URL"
    for url, action in targets:  # 每個資料庫一個交易：失敗或 dry-run 時整批回滾
        with psycopg.connect(_libpq(url)) as conn:
            report.update(action(conn))
            conn.rollback() if dry_run else conn.commit()
    if "knowledge_records" in report:
        report["next_step"] = INDEX_HINT
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Import data/seed/*.json into the app and knowledge databases")
    parser.add_argument("--seed-dir", default=str(DEFAULT_SEED_DIR))
    parser.add_argument("--dry-run", action="store_true", help="試算會新增幾筆，不寫入")
    parser.add_argument("--only", choices=("app", "knowledge"))
    args = parser.parse_args(argv)
    report = run(args.seed_dir, app_url=os.environ.get("DATABASE_URL"),
                 knowledge_url=os.environ.get("KNOWLEDGE_DATABASE_URL"), dry_run=args.dry_run, only=args.only)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
