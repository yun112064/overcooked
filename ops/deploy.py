"""T17 雲端部署：python -m ops.deploy --env-file .env.prod

固定順序：正式設定檢查 → 部署前備份（label=predeploy，並複製到獨立位置）→ 建置映像 →
alembic upgrade head（只在這裡升級；正式後端 AUTO_MIGRATE=false，啟動時由 schema 閘門確認）→ 啟動。
任何一步失敗就停止；遷移失敗不自動 downgrade，改以部署前備份還原到新資料庫（ops.restore）。
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def compose_cmd(env_file: str) -> list[str]:
    return ["docker", "compose", "--env-file", env_file, "-f", "compose.yaml", "-f", "compose.prod.yaml"]


def deploy(*, preflight, backup, run, out=print, env_file: str = ".env.prod") -> int:
    problems = preflight()
    if problems:
        out("部署中止：正式設定檢查未通過")
        for p in problems:
            out(f"  - {p}")
        return 2
    try:
        backup_path = backup()
    except Exception as exc:  # noqa: BLE001
        out(f"部署中止：部署前備份失敗（{exc}），未做任何變更")
        return 3
    out(f"部署前備份：{backup_path}")
    compose = compose_cmd(env_file)
    if run([*compose, "build"]) != 0:
        out("部署中止：建置失敗，資料庫未變更")
        return 4
    if run([*compose, "run", "--rm", "backend", "alembic", "-c", "/app/alembic.ini", "upgrade", "head"]) != 0:
        out("部署中止：Alembic 升級失敗。不要執行 downgrade；舊版服務仍可繼續使用（若 schema 未變）。")
        out("若資料庫已部分變更，以部署前備份還原到新資料庫後再切換連線：")
        out(f"  python -m ops.restore {backup_path} --target-database meal_planner_restored "
            "--data-target <新 Data Root>")
        return 5
    if run([*compose, "up", "-d", "--remove-orphans"]) != 0:
        out("啟動失敗：schema 已升級，請檢查 docker compose logs；必要時以部署前備份還原")
        return 6
    out("部署完成（不代表五人容量已驗證）")
    return 0


def main(argv=None) -> int:
    from ops.backup import _load_env, create_backup, mirror_backup
    from ops.pg import PgTools
    from ops.preflight import check

    parser = argparse.ArgumentParser(description="T17 雲端部署（含部署前備份與 Alembic 閘門）")
    parser.add_argument("--env-file", default=".env.prod")
    args = parser.parse_args(argv)
    env = _load_env(REPO_ROOT / args.env_file)

    def do_backup():
        path = create_backup("cloud", pg=PgTools(container=env.get("BACKUP_PG_CONTAINER", "meal-planner-db")),
                             database=env.get("BACKUP_DATABASE", "meal_planner"),
                             backup_root=Path(env["PROJECT_BACKUP_ROOT"]), data_root=Path(env["PROJECT_DATA_ROOT"]),
                             label="predeploy")
        if env.get("BACKUP_MIRROR_ROOT"):
            mirror_backup(path, Path(env["BACKUP_MIRROR_ROOT"]))
        return path

    def do_run(cmd):
        print("$ " + " ".join(cmd))
        return subprocess.run(cmd, cwd=REPO_ROOT).returncode

    return deploy(preflight=lambda: check(env, side="cloud")[0], backup=do_backup, run=do_run,
                  env_file=args.env_file)


if __name__ == "__main__":
    sys.exit(main())
