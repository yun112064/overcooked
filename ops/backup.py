"""T17 每日備份：python -m ops.backup {cloud|knowledge|assistant} [選項]

三份都在同一台團隊電腦上（scripts/backup-daily.ps1 每日依序執行）：
業務（cloud）：業務資料庫 pg_dump、Data Root（不含 logs/）、來源／規則快照。
知識（knowledge）：T11 營養知識庫 pg_dump、知識來源 JSONL（可據以重建）、來源／規則快照。
小幫手（assistant）：飲食小幫手資料庫 pg_dump（語料、分段、向量）、選用的原始語料目錄、來源／規則快照。

每份備份寫在 <備份根>/<side>/<UTC 時間戳>/，先寫 .partial 目錄、全部完成並寫入 manifest.json 後才改名，
因此未完成的備份不會被當成可用備份，也不會影響保留期判斷。--mirror 另複製一份到獨立位置
（另一台主機掛載的目錄或物件儲存掛載點），兩處各自套用保留期（預設 7 日，最新一份永遠保留）。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ops.env import load_env, safe_error
from ops.pg import PgTools

FORMAT = "meal-planner-backup/1"
SIDES = ("cloud", "knowledge", "assistant")
REPO_ROOT = Path(__file__).resolve().parents[1]

# 來源／規則快照：重建或比對歷史時需要的版本化檔案（缺任何一項即視為備份失敗）
SNAPSHOT_PATHS = {
    "cloud": ["config", "nutrition_target", "release_data/nutrition-v1/manifest.json",
              "release_data/nutrition-v1/policy.json", "data/recipe_dataset_1103_V3.xlsx",
              "knowledge_rag/schema.sql", "assistant_rag/schema.sql", "inference/model_manifest.json"],
    "knowledge": ["knowledge_rag/schema.sql", "knowledge_rag/current-source.json", "knowledge_rag/reviews",
                  "config/model_pricing.json"],
    "assistant": ["assistant_rag/schema.sql", "assistant_rag/exclusions.json", "assistant_rag/food_categories.json",
                  "assistant_rag/eval", "config/model_pricing.json"],
}
# 預設容器與資料庫名稱：業務庫為 compose.prod.yaml 的 meal-prod-db（資料庫名稱優先取 DATABASE_URL）；
# 知識庫與小幫手庫為 deploy/team/compose.yaml。V3 開發庫 overcooked-1102-v3-db 只能以 --container 明確指定
DEFAULTS = {
    "cloud": ("meal-prod-db", "meal_planner_prod"),
    "knowledge": ("meal-prod-knowledge-db", "meal_knowledge"),
    "assistant": ("meal-prod-assistant-db", "overcooked_assistant"),
}
CLOUD_COUNT_TABLES = ("users", "households", "household_members", "meal_requests", "generation_results",
                      "adopted_meal_plans", "recipes")
KNOWLEDGE_SOURCES_SQL = "SELECT row_to_json(r) FROM knowledge_records r ORDER BY seq"
SKIP_PARTS = {"__pycache__", "tests", ".pytest_cache"}
# 不放進備份的 Data Root 項目：Log 另有保留政策；db.env 只是本機連線密碼，還原時重新產生
DATA_ROOT_EXCLUDE = {"logs", "db.env"}
# 只在 Data Root 最上層略過：模型權重（約 2.3 GB）可依 models/*/REVISION 從官方重新下載
DATA_ROOT_TOP_EXCLUDE = DATA_ROOT_EXCLUDE | {"models"}


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(code_root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(code_root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _tar(out: Path, base: Path, entries: list[str]) -> None:
    def keep(info: tarfile.TarInfo):
        parts = Path(info.name).parts
        return None if SKIP_PARTS & set(parts) or (parts and parts[-1] in DATA_ROOT_EXCLUDE) else info

    with tarfile.open(out, "w:gz") as tar:
        for entry in entries:
            tar.add(base / entry, arcname=entry, filter=keep)


def _counts(pg: PgTools, database: str, tables) -> dict:
    return {t: int(pg.query(database, f'SELECT count(*) FROM "{t}"')[0][0]) for t in tables}


def _knowledge_counts(pg: PgTools, database: str) -> dict:
    rows = pg.query(database, "SELECT count(*), count(*) FILTER (WHERE status = 'approved') FROM knowledge_records")
    active = pg.query(database, "SELECT i.index_version, i.embedding_model, i.record_count, "
                                "(SELECT count(*) FROM knowledge_embeddings e WHERE e.index_version = i.index_version) "
                                "FROM knowledge_indexes i WHERE i.status = 'active'")
    counts = {"knowledge_records": int(rows[0][0]), "approved_records": int(rows[0][1]), "active_index": None}
    if active:
        version, model, record_count, embeddings = active[0]
        counts["active_index"] = {"index_version": version, "embedding_model": model,
                                  "record_count": int(record_count), "embeddings": int(embeddings)}
    return counts


def _vector_dimensions(pg: PgTools, database: str) -> int:
    """knowledge_embeddings.embedding 的維度（1536 或本機 embeddinggemma 的 768）；重建時 schema 要一致。"""
    return int(pg.query(database, "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'knowledge_embeddings'::regclass "
                                  "AND attname = 'embedding'")[0][0])


def _assistant_counts(pg: PgTools, database: str) -> dict:
    counts = _counts(pg, database, ("index_versions", "articles", "chunks"))
    active = pg.query(database, "SELECT version FROM index_versions WHERE active")
    counts["active_version"] = active[0][0] if active else None
    return counts


def create_backup(side: str, *, pg: PgTools, database: str, backup_root: Path, code_root: Path = REPO_ROOT,
                  data_root: Path | None = None, now: datetime | None = None, label: str | None = None) -> Path:
    if side not in SIDES:
        raise BackupError(f"side 只能是 {SIDES}")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    code_root, backup_root = Path(code_root), Path(backup_root)
    missing = [p for p in SNAPSHOT_PATHS[side] if not (code_root / p).exists()]
    if missing:
        raise BackupError(f"來源／規則快照缺少：{', '.join(missing)}")
    if side == "cloud" and (data_root is None or not Path(data_root).is_dir()):
        raise BackupError("雲端備份需要存在的 Data Root")
    if data_root is not None and not Path(data_root).is_dir():
        raise BackupError(f"Data Root 不存在：{data_root}")

    name = now.strftime("%Y%m%dT%H%M%SZ") + (f"-{label}" if label else "")
    final = backup_root / side / name
    if final.exists():
        raise BackupError(f"備份已存在：{final}")
    work = final.with_name(name + ".partial")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    manifest = {"format": FORMAT, "side": side, "label": label, "created_at": now.isoformat(),
                "database": database, "git_commit": _git_commit(code_root),
                "snapshot_paths": SNAPSHOT_PATHS[side]}
    if side == "cloud":
        manifest["alembic_revision"] = pg.query(database, "SELECT version_num FROM alembic_version")[0][0]
        manifest["counts"] = _counts(pg, database, CLOUD_COUNT_TABLES)
    elif side == "knowledge":
        manifest["counts"] = _knowledge_counts(pg, database)
        manifest["vector_dimensions"] = _vector_dimensions(pg, database)
    else:
        manifest["counts"] = _assistant_counts(pg, database)

    pg.dump(database, work / "database.dump")
    if data_root is not None:
        data_root = Path(data_root)
        entries = sorted(p.name for p in data_root.iterdir() if p.name not in DATA_ROOT_TOP_EXCLUDE)
        _tar(work / "data_root.tar.gz", data_root, entries)
        manifest["data_root_entries"] = entries
    if side == "knowledge":
        pg.copy_out(database, KNOWLEDGE_SOURCES_SQL, work / "knowledge_sources.jsonl")
    _tar(work / "snapshot.tar.gz", code_root, SNAPSHOT_PATHS[side])

    manifest["files"] = {f.name: {"sha256": _sha256(f), "size": f.stat().st_size}
                         for f in sorted(work.iterdir()) if f.is_file()}
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    work.rename(final)
    return final


def verify_backup(path: Path) -> dict:
    """核對 manifest 與每個檔案的大小及 SHA-256；不符即丟 BackupError。"""
    path = Path(path)
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"manifest 無法讀取：{exc}") from None
    if manifest.get("format") != FORMAT:
        raise BackupError("不支援的備份格式")
    for name, meta in manifest["files"].items():
        f = path / name
        if not f.is_file():
            raise BackupError(f"缺少備份檔：{name}")
        if f.stat().st_size != meta["size"] or _sha256(f) != meta["sha256"]:
            raise BackupError(f"校驗碼不符：{name}")
    return manifest


def _completed(side_root: Path) -> list[tuple[datetime, Path]]:
    found = []
    for d in side_root.iterdir() if side_root.is_dir() else []:
        if d.is_dir() and not d.name.endswith(".partial") and (d / "manifest.json").is_file():
            try:
                created = datetime.fromisoformat(json.loads((d / "manifest.json").read_text("utf-8"))["created_at"])
            except (ValueError, KeyError):
                continue
            found.append((created, d))
    return sorted(found)


def prune_backups(side_root: Path, *, keep_days: int = 7, now: datetime | None = None) -> list[Path]:
    """刪除超過保留期的已完成備份（最新一份永遠保留）與超過一天的未完成 .partial 目錄。"""
    now = now or datetime.now(timezone.utc)
    side_root = Path(side_root)
    removed = []
    cutoff = now - timedelta(days=keep_days)
    for created, d in _completed(side_root)[:-1]:
        if created < cutoff:
            shutil.rmtree(d)
            removed.append(d)
    for d in side_root.glob("*.partial") if side_root.is_dir() else []:
        if datetime.fromtimestamp(d.stat().st_mtime, timezone.utc) < now - timedelta(days=1):
            shutil.rmtree(d)
            removed.append(d)
    return removed


def mirror_backup(path: Path, mirror_root: Path) -> Path:
    """複製到獨立位置並在目的地重新核對校驗碼。"""
    path = Path(path)
    side = verify_backup(path)["side"]
    final = Path(mirror_root) / side / path.name
    work = final.with_name(path.name + ".partial")
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(path, work)
    verify_backup(work)
    work.rename(final)
    return final


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="T17 每日備份（含保留期與獨立位置副本）")
    parser.add_argument("side", choices=SIDES)
    parser.add_argument("--env-file", type=Path, help="讀取 .env（不覆寫已存在的環境變數）")
    parser.add_argument("--container", help="資料庫容器名稱（預設見 DEFAULTS）")
    parser.add_argument("--database", help="資料庫名稱")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--data-root", type=Path, help="雲端 Data Root；assistant 可指定原始語料目錄")
    parser.add_argument("--mirror", type=Path, help="獨立位置（必須不在同一顆磁碟／主機上）")
    parser.add_argument("--keep-days", type=int)
    parser.add_argument("--label", help="例如 predeploy")
    args = parser.parse_args(argv)
    env = load_env(args.env_file)
    side = args.side
    prefix = side.upper()
    default_container, default_db = DEFAULTS[side]
    container = args.container or env.get(f"BACKUP_{prefix}_CONTAINER") or default_container
    url_db = urlsplit(env.get("DATABASE_URL") or "").path.lstrip("/") if side == "cloud" else ""
    database = args.database or env.get(f"BACKUP_{prefix}_DATABASE") or url_db or default_db
    backup_root = args.backup_root or Path(env.get("PROJECT_BACKUP_ROOT") or "")
    mirror = args.mirror or (Path(env["BACKUP_MIRROR_ROOT"]) if env.get("BACKUP_MIRROR_ROOT") else None)
    keep_days = args.keep_days or int(env.get("BACKUP_RETENTION_DAYS") or 7)
    data_root = args.data_root
    if data_root is None and side == "cloud" and env.get("PROJECT_DATA_ROOT"):
        data_root = Path(env["PROJECT_DATA_ROOT"])
    if data_root is None and side == "assistant" and env.get("ASSISTANT_DATA_DIR"):
        data_root = Path(env["ASSISTANT_DATA_DIR"])
    if not str(backup_root):
        parser.error("需要 --backup-root 或 PROJECT_BACKUP_ROOT")
    try:
        path = create_backup(side, pg=PgTools(container=container), database=database, backup_root=backup_root,
                             data_root=data_root, label=args.label)
        print(f"backup ok: {path}")
        removed = prune_backups(backup_root / side, keep_days=keep_days)
        if mirror:
            copy = mirror_backup(path, mirror)
            print(f"mirror ok: {copy}")
            removed += prune_backups(mirror / side, keep_days=keep_days)
        else:
            print("警告：未設定 --mirror／BACKUP_MIRROR_ROOT，備份只在本機，不符合獨立位置要求", file=sys.stderr)
        for d in removed:
            print(f"pruned: {d}")
    except Exception as exc:  # noqa: BLE001 — 排程器只看結束碼與訊息
        print(f"backup FAILED: {safe_error(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
