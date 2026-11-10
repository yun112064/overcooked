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


def verify_disposable(engine) -> None:
    """Destructive test setup requires a local test DB and its provisioning marker."""
    url = engine.url
    if (url.get_backend_name() != 'postgresql' or url.host not in ('localhost', '127.0.0.1')
            or not (url.database or '').startswith('meal_planner_1102_v3_')
            or not url.database.endswith('_test')):
        raise RuntimeError('Refusing to rebuild a non-disposable database')
    with engine.connect() as connection:
        name, marker = connection.execute(text(
            "SELECT current_database(), shobj_description(oid,'pg_database') "
            "FROM pg_database WHERE datname=current_database()"
        )).one()
        if name != url.database or not marker or not marker.startswith(('overcooked-stage0:', 'overcooked:disposable:')):
            raise RuntimeError('Disposable test database marker required')


def migrate_fresh() -> None:
    """從空資料庫升到 head；同時驗證 downgrade 能完整退回。"""
    from alembic import command
    from alembic.config import Config

    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    verify_disposable(_engine)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    # Prior test classes can leave a confirmation journal. Clear only the
    # marker-verified fixture DB before rehearsing downgrade; the migration's
    # refusal to remove real event data remains unchanged.
    with _engine.begin() as connection:
        connection.execute(text('TRUNCATE meal_confirmation_events'))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def truncate_all() -> None:
    verify_disposable(_engine)
    with _engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename NOT IN ('alembic_version', 'recipe_data_releases')"
        )).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
        # These are migration-owned seed rows. A clean test starts with the same
        # empty access and binding state as a newly migrated database.
        if "management_access_state" in tables:
            conn.execute(text("INSERT INTO management_access_state (id, revision) VALUES ('global', 1)"))
            conn.execute(text("""
                INSERT INTO management_active_bindings (channel, revision, updated_at)
                VALUES ('recipe_catalog', 1, now()), ('ingredient_catalog', 1, now()),
                       ('price_policy', 1, now()), ('source_policy', 1, now())
            """))


# 測試用的 FOOD 字典子集（編號沿用正式食譜 SQL 資料的標準食材 ID）
FOODS = {
    "FOOD001541": ("醬油", ["豆油"]),
    "FOOD001542": ("醬油膏", []),
    "FOOD001566": ("雞蛋", ["蛋"]),
    "FOOD000039": ("大蒜", ["蒜頭"]),
    "FOOD000678": ("胡蘿蔔", ["紅蘿蔔"]),
    "FOOD000867": ("高麗菜", ["甘藍"]),
    "FOOD001016": ("番茄", []),
    "FOOD001444": ("蕃茄", []),  # 與番茄為同義組 SYN005（config/ingredient_synonyms.json）
    "FOOD000585": ("青江菜", []),
    "FOOD000004": ("九層塔", []),
}


def seed_foods() -> None:
    """truncate 之後重新放入 FOOD 字典（T07–T09 以 ingredient_aliases 作為標準食材字典）。"""
    with _engine.begin() as conn:
        conn.execute(text("UPDATE recipe_data_releases SET active = false WHERE active"))
        conn.execute(text(
            "UPDATE recipe_data_releases SET active = true WHERE release_id = 'legacy-1017'"
        ))
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
