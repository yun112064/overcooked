"""需要 PostgreSQL 的 API 測試共用工具。

只有設定 TEST_DATABASE_URL 才會執行，並以 Alembic 遷移建立資料表，
不會碰到 DATABASE_URL 指向的資料庫。CI 由 pgvector 服務提供。
"""
import os
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ORIGIN = "http://localhost:8080"

requires_db = unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")

_engine = None
_Session = None


def migrate_fresh() -> None:
    """從空資料庫升到 head；同時驗證 downgrade 能完整退回。"""
    from alembic import command
    from alembic.config import Config

    global _engine, _Session
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    _engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def truncate_all() -> None:
    with _engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        )).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))


def session_factory():
    return _Session


def make_client():
    """TestClient 以 https 連線，Secure Cookie 才會被送回。"""
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    def override_get_db():
        db = _Session()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN})
