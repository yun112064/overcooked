"""T17 還原與還原驗證：python -m ops.restore <備份目錄> --target-database <空資料庫> [選項]

只還原到「不存在或沒有任何資料表」的資料庫，絕不覆寫現有資料庫；正式切換時先還原到新庫、
驗證通過後再改連線（或停機後將舊庫改名保留），不做破壞性降版。

雲端：還原業務庫 → 比對 manifest 的 Alembic 版本與各表筆數 → 讀出家庭與已採用餐食。
團隊電腦：還原知識庫並核對筆數與 active 索引；或 --rebuild-from-sources 只用知識來源 JSONL 與
快照內的 schema.sql 重建知識紀錄（向量索引需再以 T11 build_index 經 T10 額度重建）。
"""
import argparse
import io
import json
import sys
import tarfile
from pathlib import Path

from ops.backup import CLOUD_COUNT_TABLES, BackupError, _counts, _knowledge_counts, verify_backup
from ops.pg import PgTools


class RestoreError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _prepare_target(pg: PgTools, database: str) -> None:
    if pg.database_exists(database):
        tables = pg.query(database, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        if int(tables[0][0]):
            raise RestoreError("TARGET_NOT_EMPTY", f"目標資料庫 {database} 已有資料表，拒絕覆寫")
    else:
        pg.create_db(database)


def _extract(archive: Path, target: Path) -> None:
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise RestoreError("TARGET_NOT_EMPTY", f"目標目錄 {target} 不是空的")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(target, filter="data")


def restore_backup(path: Path, *, pg: PgTools, target_database: str, data_target: Path | None = None) -> dict:
    path = Path(path)
    manifest = verify_backup(path)  # 校驗碼不符時在動到任何資料庫之前就失敗
    _prepare_target(pg, target_database)
    pg.restore(target_database, path / "database.dump")
    if data_target is not None:
        if "data_root.tar.gz" not in manifest["files"]:
            raise RestoreError("NO_DATA_ROOT", "此備份沒有 Data Root")
        _extract(path / "data_root.tar.gz", data_target)
    return manifest


def check_cloud_restore(pg: PgTools, database: str, manifest: dict) -> dict:
    problems = []
    revision = pg.query(database, "SELECT version_num FROM alembic_version")[0][0]
    if revision != manifest["alembic_revision"]:
        problems.append(f"Alembic 版本 {revision} ≠ 備份 {manifest['alembic_revision']}")
    counts = _counts(pg, database, CLOUD_COUNT_TABLES)
    for table, expected in manifest["counts"].items():
        if counts.get(table) != expected:
            problems.append(f"{table} 筆數 {counts.get(table)} ≠ 備份 {expected}")
    # 實際讀出家庭與已採用餐食（含 JSONB 菜名），確認不只是表存在
    households = pg.query(database, "SELECT count(*) FROM households h WHERE h.name IS NOT NULL")
    meals = pg.query(database, "SELECT a.meal_date, d->>'dish_name' FROM adopted_meal_plans a "
                               "JOIN households h ON h.id = a.household_id "
                               "CROSS JOIN LATERAL jsonb_array_elements(a.dishes::jsonb) d "
                               "ORDER BY a.meal_date, d->>'dish_name'")
    if int(households[0][0]) != manifest["counts"].get("households"):
        problems.append("家庭資料無法完整讀取")
    return {"ok": not problems, "problems": problems, "alembic_revision": revision, "counts": counts,
            "adopted_dates": sorted({m[0] for m in meals}), "adopted_dishes": [m[1] for m in meals]}


def check_knowledge_restore(pg: PgTools, database: str, manifest: dict) -> dict:
    counts = _knowledge_counts(pg, database)
    problems = [f"{k}：{counts.get(k)} ≠ 備份 {v}" for k, v in manifest["counts"].items() if counts.get(k) != v]
    active = counts["active_index"]
    if active and active["embeddings"] != active["record_count"]:
        problems.append("active 索引向量數與紀錄數不符")
    return {"ok": not problems, "problems": problems, "counts": counts}


def _copy_escape(line: str) -> str:
    return line.replace("\\", "\\\\")


def rebuild_knowledge_from_sources(path: Path, *, pg: PgTools, target_database: str) -> dict:
    """只用知識來源 JSONL 與備份內的 schema.sql 重建知識紀錄（保留審核狀態與來源版本）。"""
    path = Path(path)
    manifest = verify_backup(path)
    if manifest["side"] != "knowledge":
        raise RestoreError("WRONG_SIDE", "需要團隊電腦知識庫備份")
    with tarfile.open(path / "snapshot.tar.gz", "r:gz") as tar:
        schema = tar.extractfile("knowledge_rag/schema.sql").read().decode("utf-8")
    _prepare_target(pg, target_database)
    pg.run_sql(target_database, schema)
    lines = (path / "knowledge_sources.jsonl").read_text(encoding="utf-8").splitlines()
    buf = io.StringIO()
    buf.write("CREATE TEMP TABLE src (doc jsonb);\nCOPY src (doc) FROM STDIN;\n")
    for line in lines:
        if line.strip():
            buf.write(_copy_escape(line) + "\n")
    buf.write("\\.\n")
    buf.write("INSERT INTO knowledge_records SELECT (jsonb_populate_record(NULL::knowledge_records, doc)).* "
              "FROM src ORDER BY (doc->>'seq')::bigint;\n"
              "SELECT setval(pg_get_serial_sequence('knowledge_records', 'seq'), "
              "COALESCE((SELECT max(seq) FROM knowledge_records), 1));\n")
    pg.run_sql(target_database, buf.getvalue())
    counts = _knowledge_counts(pg, target_database)
    problems = [f"{k}：{counts[k]} ≠ 備份 {manifest['counts'][k]}"
                for k in ("knowledge_records", "approved_records") if counts[k] != manifest["counts"][k]]
    return {"ok": not problems, "problems": problems, "counts": counts,
            "index_to_rebuild": manifest["counts"]["active_index"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="T17 還原到空資料庫並驗證")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--container", help="資料庫容器名稱")
    parser.add_argument("--data-target", type=Path, help="Data Root 還原目錄（必須不存在或為空）")
    parser.add_argument("--rebuild-from-sources", action="store_true", help="知識庫：只用來源 JSONL 重建")
    args = parser.parse_args(argv)
    try:
        manifest = verify_backup(args.backup)
        side = manifest["side"]
        pg = PgTools(container=args.container or ("meal-planner-db" if side == "cloud" else "meal-knowledge-db"))
        if args.rebuild_from_sources:
            report = rebuild_knowledge_from_sources(args.backup, pg=pg, target_database=args.target_database)
        else:
            manifest = restore_backup(args.backup, pg=pg, target_database=args.target_database,
                                      data_target=args.data_target)
            check = check_cloud_restore if side == "cloud" else check_knowledge_restore
            report = check(pg, args.target_database, manifest)
    except (BackupError, RestoreError) as exc:
        print(f"restore FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
