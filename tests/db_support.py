"""需要 PostgreSQL 的 API 測試共用工具。

只有設定 TEST_DATABASE_URL 才會執行，並以 Alembic 遷移建立資料表，
不會碰到 DATABASE_URL 指向的資料庫。CI 由 pgvector 服務提供。
"""
import json
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


# 測試用的 FOOD 字典子集（編號取自 recipe_dataset_1017.xlsx 的「標準食材整合」）
FOODS = {
    "FOOD001541": ("醬油", ["豆油"]),
    "FOOD001542": ("醬油膏", []),
    "FOOD001566": ("雞蛋", ["蛋"]),
    "FOOD000039": ("大蒜", ["蒜頭"]),
    "FOOD000678": ("胡蘿蔔", ["紅蘿蔔"]),
    "FOOD000867": ("高麗菜", ["甘藍"]),
    "FOOD001016": ("番茄", []),
    "FOOD000585": ("青江菜", []),
    "FOOD000004": ("九層塔", []),
}


def seed_foods() -> None:
    """truncate 之後重新放入 FOOD 字典（T07–T09 以 ingredient_aliases 作為標準食材字典）。"""
    with _engine.begin() as conn:
        for food_id, (name, aliases) in FOODS.items():
            conn.execute(text(
                "INSERT INTO ingredient_aliases (food_id, canonical_name, aliases, ambiguous_aliases, review_status, quality_issues) "
                "VALUES (:id, :name, CAST(:aliases AS json), '[]', 'pending', '[]') ON CONFLICT (food_id) DO NOTHING"
            ), {"id": food_id, "name": name, "aliases": json.dumps(aliases, ensure_ascii=False)})


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
