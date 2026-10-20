"""T17 Schema 閘門：python -m app.core.schema_gate

正式環境（AUTO_MIGRATE=false）後端啟動前執行：資料庫 Alembic 版本必須等於程式碼的 head，
否則拒絕啟動（結束碼 1）。升級只由部署腳本在部署前備份後執行（python -m ops.deploy）。
"""
import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[3]


def schema_status(database_url: str) -> dict:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    heads = sorted(ScriptDirectory.from_config(cfg).get_heads())
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_heads()
    finally:
        engine.dispose()
    current = sorted(current)
    return {"current": current[0] if len(current) == 1 else current or None, "heads": heads,
            "at_head": current == heads}


def main(database_url: str | None = None) -> int:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    status = schema_status(database_url)
    if not status["at_head"]:
        print(f"schema gate: 資料庫版本 {status['current']} 不是 head {status['heads']}；"
              "請先執行部署腳本（部署前備份後再 alembic upgrade），拒絕啟動。", file=sys.stderr)
        return 1
    print(f"schema gate: ok ({status['current']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
