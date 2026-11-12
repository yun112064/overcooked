"""T17 部署前備份與 schema 升級：python -m ops.deploy --env-file .env.prod（scripts/prod-up.ps1 呼叫）

固定順序：設定檢查 → 部署前備份（label=predeploy，並複製到獨立位置）→ alembic upgrade head。
任何一步失敗就停止；遷移失敗不自動 downgrade，改以部署前備份還原到新資料庫（ops.restore）。
建置映像、啟動服務與快速通道由 prod-up 負責；正式後端 AUTO_MIGRATE=false，只在這裡升級，
啟動時由 schema 閘門確認在 head。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]


def compose_cmd(env_file: str) -> list[str]:
    return ["docker", "compose", "--env-file", env_file, "-f", "compose.yaml", "-f", "compose.prod.yaml"]


def database_name(env: dict) -> str:
    """部署前要備份的業務庫：BACKUP_CLOUD_DATABASE，未設定時取 DATABASE_URL 的資料庫名稱。"""
    return env.get("BACKUP_CLOUD_DATABASE") or urlsplit(env.get("DATABASE_URL") or "").path.lstrip("/")


def predeploy_backup(*, pg, database: str, backup_root: Path, data_root: Path, mirror_root: Path | None,
                     now=None) -> Path | None:
    """部署前備份（label=predeploy）並複製到獨立位置；尚無資料表的資料庫沒有可備份的資料，回 None。"""
    from ops.backup import create_backup, mirror_backup
    if not pg.database_exists(database):
        return None
    if not int(pg.query(database, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")[0][0]):
        return None
    path = create_backup("cloud", pg=pg, database=database, backup_root=backup_root, data_root=data_root,
                         now=now, label="predeploy")
    if mirror_root:
        mirror_backup(path, mirror_root)
    return path


def deploy(*, preflight, backup, run, out=print, env_file: str = ".env.prod", migrate_env: dict | None = None) -> int:
    """migrate_env：只給 alembic 那一步的環境變數（管理連線；應用程式的受限角色沒有 DDL 權限）。"""
    problems = preflight()
    if problems:
        out("中止：設定檢查未通過")
        for p in problems:
            out(f"  - {p}")
        return 2
    try:
        backup_path = backup()
    except Exception as exc:  # noqa: BLE001
        from ops.env import safe_error
        out(f"中止：部署前備份失敗（{safe_error(exc)}），未做任何變更")
        return 3
    out(f"部署前備份：{backup_path}" if backup_path else "部署前備份：資料庫尚無資料表，不需備份")
    if run([*compose_cmd(env_file), "run", "--rm", "--no-deps", "-e", "DATABASE_URL", "backend", "alembic", "-c",
            "/app/alembic.ini", "upgrade", "head"], env=migrate_env) != 0:
        out("中止：Alembic 升級失敗。不要執行 downgrade；若資料庫已部分變更，以部署前備份還原到新資料庫後再切換 DATABASE_URL：")
        out(f"  python -m ops.restore {backup_path} --target-database <新資料庫名稱> --data-target <新 Data Root>")
        return 5
    out("schema 已在 head（不代表五人容量已驗證）")
    return 0


def main(argv=None) -> int:
    from ops.backup import DEFAULTS
    from ops.env import load_env
    from ops.pg import PgTools
    from ops.preflight import check

    parser = argparse.ArgumentParser(description="T17 部署前備份與 Alembic 升級")
    parser.add_argument("--env-file", default=".env.prod")
    args = parser.parse_args(argv)
    env = load_env(REPO_ROOT / args.env_file)

    def do_backup():
        pg = PgTools(container=env.get("BACKUP_CLOUD_CONTAINER") or DEFAULTS["cloud"][0])
        return predeploy_backup(pg=pg, database=database_name(env), backup_root=Path(env["PROJECT_BACKUP_ROOT"]),
                                data_root=Path(env["PROJECT_DATA_ROOT"]),
                                mirror_root=Path(env["BACKUP_MIRROR_ROOT"]) if env.get("BACKUP_MIRROR_ROOT") else None)

    def do_run(cmd, env=None):
        print("$ " + " ".join(cmd))
        return subprocess.run(cmd, cwd=REPO_ROOT, env={**os.environ, **env} if env else None).returncode

    # 管理連線只放進 alembic 那個子程序的環境變數（docker compose -e DATABASE_URL 由此帶入），不出現在指令列
    migrate_env = {"DATABASE_URL": f"postgresql+psycopg://postgres:{env.get('POSTGRES_PASSWORD', '')}@db:5432/"
                                   f"{database_name(env)}"}

    return deploy(preflight=lambda: check(env)[0], backup=do_backup, run=do_run,
                  env_file=args.env_file, migrate_env=migrate_env)


if __name__ == "__main__":
    sys.exit(main())
