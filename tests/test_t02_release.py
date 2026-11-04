"""T02 provenance, source fidelity and T13 behavior; no retailer requests."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace
import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.meal_calc.types import IngredientRequirement
from app.models.recipe import IngredientAlias
from app.services.meal_cost import latest_quote, latest_success_quotes, meal_cost_from_db
from app.source_adapters.price_import import (PriceBundle, RUN_FIELDS, QUOTE_FIELDS,
    load_bundle, binding_plan, validate_bundle, install, _assert_target)
from app.source_adapters.price_models import PriceQuote
from app.source_adapters.price_scope import materialize_quote
from app.source_adapters.pricing_repository import PricingRepository, RunLease

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
OLD = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
FOOD = 'FOOD000005'
NAME = '合成高麗菜'
MANIFEST = Path(__file__).resolve().parents[1] / 'data/pricing/t02-history-20261103/manifest.json'
MANIFEST_HASH = 'ddb85b32c369aadaffbdd4f97933240c2fb8df4ce6c2028e63cfe3122e0cede0'


def synthetic_bundle():
    quote = PriceQuote(canonical_name=NAME, food_ids=[FOOD], source_url='https://example.invalid/price',
        queried_at=OLD, status='success', selection_tier='auto_adopted', confidence='high',
        product_id='SYNTHETIC', product_name=NAME + ' 300g', price='60', currency='TWD',
        package_quantity='300', package_unit='g', availability='in_stock')
    payload = quote.model_dump(mode='json')
    run = {field: 0 for field in RUN_FIELDS}
    run.update(id='10000000-0000-0000-0000-000000000001', run_key='synthetic-history',
        provider='pxmart', trigger_type='manual', scheduled_for=OLD.isoformat(), started_at=OLD.isoformat(),
        finished_at=OLD.isoformat(), status='completed', ingredient_total=1, success_count=1,
        fatal_error=None, status_counts={'success': 1}, missing_reasons={})
    row = {field: payload.get(field) for field in QUOTE_FIELDS}
    row.update(quote_payload=payload, run_key=run['run_key'], provider='pxmart')
    manifest = {'release_id': 'synthetic-history-v1', 'recipe_release_id': 'legacy-1017',
                'source_ref': '0' * 40, 'usage_status': 'historical_requires_refresh'}
    return PriceBundle(manifest, '1' * 64, [run], [row])


def test_real_price_package_has_only_two_hash_verified_sources():
    bundle = load_bundle(MANIFEST, MANIFEST_HASH)
    assert len(bundle.runs) == 8 and len(bundle.quotes) == 2279
    assert all(row['valid_until'] is None for row in bundle.quotes)
    assert set(bundle.manifest['files']) == {'price_collection_runs.json', 'price_quote_snapshots.json'}


def test_rejects_manifest_hash_mismatch():
    with pytest.raises(ValueError, match='manifest hash'):
        load_bundle(MANIFEST, '0' * 64)


def test_rejects_scalar_price_different_from_payload():
    bundle = synthetic_bundle()
    bundle.quotes[0]['price'] = '61'
    with pytest.raises(ValueError, match='payload/scalar mismatch: price'):
        validate_bundle(bundle)


def test_rejects_duplicate_rows_and_wrong_providers():
    bundle = synthetic_bundle()
    with pytest.raises(ValueError, match='Duplicate source snapshot'):
        validate_bundle(replace(bundle, quotes=bundle.quotes * 2))
    bundle.quotes[0]['provider'] = 'uniprosperity'
    with pytest.raises(ValueError, match='provider differs'):
        validate_bundle(bundle)


def test_binding_requires_both_food_id_and_canonical_name():
    bundle = synthetic_bundle()
    bindings, unresolved = binding_plan(bundle, {FOOD: '另一種菜', 'FOOD000006': NAME})
    assert not any(bindings.values())
    assert unresolved[0]['reason'] == 'canonical_name_mismatch'
    bindings, unresolved = binding_plan(bundle, {FOOD: NAME})
    assert list(bindings.values()) == [[FOOD]] and unresolved == []


def test_history_without_expiry_does_not_become_a_current_price():
    source = synthetic_bundle().quotes[0]['quote_payload']
    projected = materialize_quote({'quote_payload': source, 'id': 1,
        'recipe_release_id': 'legacy-1017', 'price_data_release_id': 'synthetic-history-v1',
        'usage_status': 'historical_requires_refresh'})
    assert projected.price is None
    assert projected.missing_reason == 'PRICE_REVALIDATION_REQUIRED'
    assert projected.queried_at == OLD and projected.valid_until is None
    assert source['price'] == '60'  # Original source remains unchanged.


def test_live_quote_without_source_expiry_uses_local_freshness_window():
    from datetime import timedelta

    source = synthetic_bundle().quotes[0]['quote_payload']
    quote = materialize_quote({'quote_payload': source, 'id': 1,
        'recipe_release_id': 'legacy-1017', 'price_data_release_id': None, 'usage_status': None})
    assert quote.valid_until == OLD + timedelta(hours=48)
    assert source['valid_until'] is None


def test_result_projection_keeps_price_provenance_and_missing_reason():
    from app.services.meal_results import _prices
    payload = {'cost': {'status': 'computed', 'lines': [{
        'food_id': FOOD, 'shortage_grams': '100', 'price_status': 'unknown',
        'missing_reason': 'PRICE_REVALIDATION_REQUIRED',
        'quote': {'recipe_release_id': 'synthetic-recipe', 'price_data_release_id': 'synthetic-history',
                  'snapshot_id': 12, 'usage_status': 'historical_requires_refresh'},
    }]}}
    item = _prices(payload)['items'][0]
    assert item['price_data_release_id'] == 'synthetic-history' and item['snapshot_id'] == 12
    assert item['missing_reason'] == 'PRICE_REVALIDATION_REQUIRED' and item['cost'] is None


def test_projection_exposes_only_the_verified_food_id():
    source = synthetic_bundle().quotes[0]['quote_payload']
    source['food_ids'] = [FOOD, 'FOOD000006']
    quote = materialize_quote({'quote_payload': source, 'id': 1, 'food_id': FOOD,
        'recipe_release_id': 'legacy-1017', 'price_data_release_id': 'synthetic-history-v1',
        'usage_status': 'historical_requires_refresh'})
    assert quote.food_ids == [FOOD]
    assert source['food_ids'] == [FOOD, 'FOOD000006']


@pytest.mark.parametrize('database', ['meal_planner_1102_v1', 'meal_planner', 'other_project'])
def test_import_rejects_other_databases(database):
    connection = MagicMock()
    connection.scalar.return_value = database
    with pytest.raises(ValueError, match='restricted'):
        _assert_target(connection)
    connection.execute.assert_not_called()


@pytest.fixture
def pg_connection():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Verified disposable V3 TEST_DATABASE_URL not set')
    engine = create_engine(url)
    # No generic database URL is permitted, even if a caller accidentally sets it.
    if engine.url.database != 'meal_planner_1102_v3_t02_test':
        raise RuntimeError('This test only accepts the dedicated disposable T02 database')
    with engine.connect() as connection:
        with connection.begin() as transaction:
            assert connection.scalar(text('SELECT current_database()')) == 'meal_planner_1102_v3_t02_test'
            connection.execute(text("UPDATE recipe_data_releases SET active = true WHERE release_id = 'legacy-1017'"))
            connection.execute(IngredientAlias.__table__.insert(), {'food_id': FOOD, 'canonical_name': NAME})
            yield connection
            transaction.rollback()
    engine.dispose()


def test_pg_import_is_exact_idempotent_and_historical_cost_is_unknown(pg_connection):
    connection = pg_connection
    bundle = synthetic_bundle()
    assert install(connection, bundle)['status'] == 'installed'
    assert install(connection, bundle)['status'] == 'unchanged'
    assert connection.scalar(text('SELECT count(*) FROM price_quote_snapshots')) == 1
    with Session(bind=connection) as session:
        quote = latest_quote(session, FOOD, now=NOW)
        assert quote.price is None and quote.queried_at == OLD
        assert latest_success_quotes(session, now=NOW) == {}
        cost = meal_cost_from_db(session, [IngredientRequirement(food_id=FOOD, grams=Decimal('100'))],
                                [], Decimal('100'), now=NOW)
        assert cost.purchase_total is None and cost.cost_total is None
        assert cost.lines[0].missing_reason == 'PRICE_REVALIDATION_REQUIRED'
    stored = connection.scalar(text('SELECT quote_payload FROM price_quote_snapshots'))
    assert stored == bundle.quotes[0]['quote_payload']


def test_pg_existing_source_tampering_is_rejected(pg_connection):
    install(pg_connection, synthetic_bundle())
    pg_connection.execute(text('UPDATE price_quote_snapshots SET price = 61'))
    with pytest.raises(ValueError, match='Stored snapshot differs'):
        install(pg_connection, synthetic_bundle())


def test_pg_mid_import_error_can_roll_back_without_partial_rows(pg_connection):
    with pytest.raises(RuntimeError):
        with pg_connection.begin_nested():
            install(pg_connection, synthetic_bundle())
            raise RuntimeError('synthetic interruption before commit')
    assert pg_connection.scalar(text('SELECT count(*) FROM price_data_releases')) == 0
    assert pg_connection.scalar(text('SELECT count(*) FROM price_collection_runs')) == 0


def test_pg_unbound_rows_cannot_leak_into_active_recipe(pg_connection):
    # A run for an active recipe still needs an exact Food_ID binding.
    install(pg_connection, synthetic_bundle())
    pg_connection.execute(text('DELETE FROM price_snapshot_foods'))
    with Session(bind=pg_connection) as session:
        assert latest_quote(session, FOOD, now=NOW) is None


def test_pg_live_quote_binding_preserves_two_costs_and_filters_history(pg_connection):
    connection = pg_connection
    install(connection, synthetic_bundle())
    run_id = '20000000-0000-0000-0000-000000000001'
    connection.execute(text("INSERT INTO price_collection_runs (id,run_key,provider,trigger_type,scheduled_for,"
        "started_at,status,recipe_release_id) VALUES (:id,'synthetic-live','pxmart','manual',:now,:now,'running','legacy-1017')"),
        {'id': run_id, 'now': NOW})
    live = PriceQuote.model_validate({**synthetic_bundle().quotes[0]['quote_payload'], 'queried_at': NOW})
    # Exercise the real collector repository without committing this test's outer transaction.
    local = SimpleNamespace(execute=connection.execute, commit=lambda: None)
    lease = RunLease(run_id, 'synthetic-live', local, recipe_release_id='legacy-1017')
    repository = PricingRepository(connection.engine)
    repository.save_quote(lease, live)
    assert connection.scalar(text('SELECT count(*) FROM price_snapshot_foods WHERE snapshot_id IN '
                                   '(SELECT id FROM price_quote_snapshots WHERE run_id=:run)'), {'run': run_id}) == 1
    with Session(bind=connection) as session:
        cost = meal_cost_from_db(session, [IngredientRequirement(food_id=FOOD, grams=Decimal('100'))],
                                [], Decimal('100'), now=NOW)
        assert cost.purchase_total == Decimal('60') and cost.cost_total == Decimal('20')
        assert FOOD in latest_success_quotes(session, now=NOW)
    connection.execute(text("UPDATE recipe_data_releases SET active = false WHERE release_id='legacy-1017'"))
    with pytest.raises(ValueError, match='release changed'):
        repository.save_quote(lease, live)


def test_pg_verified_net_mass_applies_to_the_exact_live_package_only(pg_connection):
    from scripts.install_t02_package_mass import install as install_mass

    conn = pg_connection
    local = SimpleNamespace(execute=conn.execute, commit=lambda: None)
    repository = PricingRepository(conn.engine)
    payload = synthetic_bundle().quotes[0]['quote_payload']
    live = PriceQuote.model_validate({**payload, 'queried_at': NOW, 'product_id': 'sku-1',
                                     'product_name': 'Test bottle', 'package_unit': 'ml'})

    def save(run_key, quote):
        run_id = conn.scalar(text("""
            INSERT INTO price_collection_runs (id,run_key,provider,trigger_type,scheduled_for,started_at,status,recipe_release_id)
            VALUES (gen_random_uuid(), :key, 'pxmart', 'manual', :now, :now, 'completed', 'legacy-1017') RETURNING id
        """), {'key': run_key, 'now': NOW})
        lease = RunLease(run_id, run_key, local, recipe_release_id='legacy-1017')
        repository.save_quote(lease, quote)
        return conn.scalar(text('SELECT id FROM price_quote_snapshots WHERE run_id=:id'), {'id': run_id})

    snapshot = save('mass-source', live)
    manifest = {'run_key': 'mass-source', 'recipe_release_id': 'legacy-1017', 'packages': [{
        'source_snapshot_id': snapshot, 'food_id': FOOD, 'provider': 'pxmart', 'product_id': live.product_id,
        'product_name': live.product_name, 'volume_ml': str(live.package_quantity), 'net_mass_g': '250',
        'source_url': 'https://example.test/manufacturer', 'source_detail': 'net mass on exact package',
    }]}
    assert install_mass(conn, manifest) == 1
    assert install_mass(conn, manifest) == 0
    raw = conn.execute(text('SELECT package_unit, quote_payload FROM price_quote_snapshots WHERE id=:id'),
                       {'id': snapshot}).one()
    assert raw.package_unit == raw.quote_payload['package_unit'] == 'ml'
    lease = RunLease('fallback', 'fallback', local, recipe_release_id='legacy-1017')
    assert repository.fallback_targets(lease, primary_run_key='mass-source') == (set(), True)
    with Session(bind=conn) as session:
        enriched = latest_quote(session, FOOD, now=NOW)
        assert (enriched.package_quantity, enriched.package_unit, enriched.queried_at) == (Decimal('250'), 'g', NOW)
        assert 'https://example.test/manufacturer' in enriched.selection_reasons[-1]
        save('changed-volume', live.model_copy(update={'package_quantity': live.package_quantity + 1}))
        assert latest_quote(session, FOOD, now=NOW).package_unit == 'ml'
