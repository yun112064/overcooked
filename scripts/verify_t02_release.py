"""Read-only verification of the installed V3 T02 release and preserved T01 data."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.meal_calc.types import IngredientRequirement
from app.services.meal_cost import latest_quote, latest_success_quotes, meal_cost_from_db
from app.source_adapters.price_import import load_bundle, binding_plan, verify_installed


def main():
    folder = ROOT / 'docs/1102_V3/T02'
    before = json.loads((folder / 'before.json').read_text(encoding='utf-8'))
    bundle = load_bundle(ROOT / 'data/pricing/t02-history-20261103/manifest.json',
                         'ddb85b32c369aadaffbdd4f97933240c2fb8df4ce6c2028e63cfe3122e0cede0')
    engine = create_engine(settings.database_url)
    if (engine.url.database != 'meal_planner_1102_v3' or engine.url.host != '127.0.0.1'
            or engine.url.port != 55435):
        raise ValueError('Verification is restricted to the dedicated local V3 database')
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        assert connection.scalar(text('SELECT current_database()')) == 'meal_planner_1102_v3'
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'd1103b02a001'
        tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars()
        counts = {t: connection.scalar(text('SELECT count(*) FROM "' + t + '"')) for t in tables}
        digests = {}
        for table in before['t01_digests']:
            rows = connection.execute(text('SELECT to_jsonb(t) FROM "' + table + '" t ORDER BY to_jsonb(t)::text')).scalars().all()
            digests[table] = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert digests == before['t01_digests'], 'T01 contents changed'
        unchanged = {t: counts[t] == n for t, n in before['table_counts'].items()
                     if t not in {'price_collection_runs', 'price_quote_snapshots'}}
        assert all(unchanged.values()), 'Non-T02 table counts changed'
        catalog = dict(connection.execute(text('SELECT food_id,canonical_name FROM ingredient_aliases '
                                               'WHERE recipe_release_id=:release'),
            {'release': bundle.manifest['recipe_release_id']}).all())
        bindings, unresolved = binding_plan(bundle, catalog)
        verify_installed(connection, bundle, bindings)
        used = set(connection.execute(text('SELECT DISTINCT food_id FROM recipe_ingredients '
                                            'WHERE recipe_release_id=:release'),
            {'release': bundle.manifest['recipe_release_id']}).scalars())
        bound = {fid for ids in bindings.values() for fid in ids}
        historical_success = set(connection.execute(text(
            "SELECT DISTINCT f.food_id FROM price_snapshot_foods f JOIN price_quote_snapshots s ON s.id=f.snapshot_id "
            "WHERE f.recipe_release_id=:release AND s.status='success'"),
            {'release': bundle.manifest['recipe_release_id']}).scalars())
        success_food = connection.scalar(text(
            "SELECT f.food_id FROM price_snapshot_foods f JOIN price_quote_snapshots s ON s.id=f.snapshot_id "
            "WHERE s.status='success' AND s.package_unit='g' ORDER BY f.food_id LIMIT 1"))
        with Session(bind=connection) as session:
            quote = latest_quote(session, success_food, now=now)
            assert quote is not None and quote.price is None and quote.missing_reason == 'PRICE_REVALIDATION_REQUIRED'
            assert latest_success_quotes(session, now=now) == {}, 'Historical prices entered budget-floor calculations'
            requirement = IngredientRequirement(food_id=success_food, grams=Decimal('100'))
            unknown = meal_cost_from_db(session, [requirement], [], Decimal('100'), now=now)
            owned = meal_cost_from_db(session, [requirement], [success_food], Decimal('100'), now=now)
            assert unknown.cost_total is None and unknown.purchase_total is None
            assert owned.cost_total == 0 and owned.purchase_total == 0
            runtime_example = {'food_id': success_food, 'query_time_preserved': quote.queried_at.isoformat(),
                               'quote_status': quote.status.value, 'missing_reason': quote.missing_reason,
                               'purchase_total': None, 'cost_total': None,
                               'owned_purchase_total': str(owned.purchase_total), 'owned_cost_total': str(owned.cost_total)}
        query_range = connection.execute(text('SELECT min(queried_at),max(queried_at) FROM price_quote_snapshots')).one()
        expiry_nulls = connection.scalar(text('SELECT count(*) FROM price_quote_snapshots WHERE valid_until IS NULL'))
        result = {'database': 'meal_planner_1102_v3', 'head': 'd1103b02a001',
            'checked_at_taipei': now.astimezone(ZoneInfo('Asia/Taipei')).isoformat(),
            'release_id': bundle.manifest['release_id'], 'table_counts': counts,
            't01_hashes_unchanged': True, 't01_digests': digests,
            'non_t02_table_counts_unchanged': unchanged, 'source_scalar_and_payload_comparison': 'PASS',
            'recipe_food_ids': len(used), 'catalog_food_ids_with_snapshot': len(bound),
            'recipe_food_ids_with_snapshot': len(used & bound),
            'catalog_food_ids_with_historical_success': len(historical_success),
            'recipe_food_ids_with_historical_success': len(used & historical_success),
            'recipe_food_ids_without_snapshot': [{'food_id': fid, 'name': catalog[fid]} for fid in sorted(used - bound)],
            'unresolved_binding_rows': len(unresolved),
            'unresolved_distinct_food_ids': len({r['food_id'] for r in unresolved}),
            'source_query_range': [value.isoformat() for value in query_range],
            'source_expiry_null_rows': expiry_nulls, 'current_usable_food_prices': 0,
            'runtime_cost_example': runtime_example, 'retailer_product_mappings_imported': 0}
        connection.rollback()
    (folder / 'database-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in {'table_counts', 't01_digests',
        'non_t02_table_counts_unchanged', 'recipe_food_ids_without_snapshot'}}, ensure_ascii=True))
    engine.dispose()


if __name__ == '__main__':
    main()
