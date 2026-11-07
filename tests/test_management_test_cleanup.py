"""Regression for migration-owned management seeds after legacy test cleanup."""
import pytest
import sqlalchemy as sa

import db_support


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
