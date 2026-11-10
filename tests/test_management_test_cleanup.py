"""Regression for migration-owned management seeds after legacy test cleanup."""
import pytest
import sqlalchemy as sa

import db_support


@pytest.mark.parametrize('url', [
    'postgresql://localhost/meal_planner_1102_v3',
    'postgresql://localhost/meal_planner_v1',
    'postgresql://remote.invalid/meal_planner_1102_v3_ci_test',
    'sqlite://',
])
def test_cleanup_guard_refuses_runtime_or_remote_targets_without_connecting(url):
    from types import SimpleNamespace
    from unittest.mock import Mock
    engine = SimpleNamespace(url=sa.engine.make_url(url), connect=Mock())
    with pytest.raises(RuntimeError, match='non-disposable'):
        db_support.verify_disposable(engine)
    engine.connect.assert_not_called()


@pytest.mark.skipif(not db_support.TEST_DATABASE_URL, reason="Disposable PostgreSQL test database required")
def test_rebuild_cleans_fixture_events_but_migration_protects_existing_events():
    from uuid import uuid4
    from alembic import command
    from alembic.config import Config
    db_support.migrate_fresh()
    engine = sa.create_engine(db_support.TEST_DATABASE_URL)
    try:
        db_support.verify_disposable(engine)
        household, plan = uuid4(), uuid4()
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO households (id,name,created_at) VALUES (:id,'fixture',now())"), {'id': household})
            c.execute(sa.text("""INSERT INTO adopted_meal_plans
                (id,household_id,meal_date,meal_type,mode,status,version,dishes,adopted_at,snapshot)
                VALUES (:id,:household,'2026-11-10','lunch','database_menu','adopted',1,'[]',now(),'{}')"""),
                {'id': plan, 'household': household})
            c.execute(sa.text("INSERT INTO meal_confirmation_events VALUES (:id,1,'confirmed',now())"), {'id': plan})
        cfg = Config(str(db_support.ROOT / 'alembic.ini'))
        cfg.attributes.update(database_url=db_support.TEST_DATABASE_URL, configure_logger=False)
        with pytest.raises(RuntimeError, match='Meal confirmation events exist'):
            command.downgrade(cfg, 'm1107management1')
        with engine.connect() as c:
            assert c.scalar(sa.text('SELECT count(*) FROM meal_confirmation_events')) == 1
        db_support.migrate_fresh()
        with engine.connect() as c:
            assert c.scalar(sa.text('SELECT count(*) FROM meal_confirmation_events')) == 0
            assert c.execute(sa.text('SELECT id,revision FROM management_access_state')).all() == [('global', 1)]
            assert c.scalar(sa.text('SELECT count(*) FROM management_active_bindings WHERE publication_id IS NULL')) == 4
    finally:
        engine.dispose()


@pytest.mark.skipif(not db_support.TEST_DATABASE_URL, reason="Disposable PostgreSQL test database required")
def test_legacy_cleanup_restores_empty_management_seed(monkeypatch):
    engine = sa.create_engine(db_support.TEST_DATABASE_URL)
    try:
        if not engine.url.database.endswith('_test'):
            pytest.skip("A dedicated disposable test database is required")
        with engine.connect() as conn:
            marker = conn.scalar(sa.text(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))
            if not marker or not marker.startswith('overcooked'):
                pytest.skip("Provisioned disposable database marker required")
        monkeypatch.setattr(db_support, '_engine', engine)
        db_support.truncate_all()
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT id, revision FROM management_access_state")).all() == [('global', 1)]
            bindings = conn.execute(sa.text(
                "SELECT channel, revision, publication_id FROM management_active_bindings ORDER BY channel")).all()
            assert bindings == [
                ('ingredient_catalog', 1, None), ('price_policy', 1, None),
                ('recipe_catalog', 1, None), ('source_policy', 1, None),
            ]
            assert conn.scalar(sa.text("SELECT count(*) FROM management_role_grants")) == 0
    finally:
        engine.dispose()
