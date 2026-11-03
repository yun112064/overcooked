"""Version-aware T02 reads. Import time never makes a historical quote current."""
from .price_models import PriceQuote, QuoteStatus, SelectionTier

# Unversioned rows are readable only in legacy fixtures without an active recipe
# release. Once T01 is published, both the run and food binding must match it.
SCOPED_QUOTES_SQL = """
WITH scoped_quotes AS (
    SELECT s.*, f.food_id, p.usage_status,
           r.recipe_release_id, r.price_data_release_id
    FROM price_quote_snapshots s
    JOIN price_collection_runs r ON r.id = s.run_id
    JOIN recipe_data_releases a ON a.release_id = r.recipe_release_id AND a.active
    JOIN price_snapshot_foods f ON f.snapshot_id = s.id AND f.recipe_release_id = a.release_id
    LEFT JOIN price_data_releases p ON p.release_id = r.price_data_release_id
    WHERE r.price_data_release_id IS NULL
       OR (p.active AND p.recipe_release_id = a.release_id)
    UNION ALL
    SELECT s.*, f.food_id, NULL::varchar AS usage_status,
           r.recipe_release_id, r.price_data_release_id
    FROM price_quote_snapshots s
    JOIN price_collection_runs r ON r.id = s.run_id
    CROSS JOIN LATERAL jsonb_array_elements_text(s.quote_payload -> 'food_ids') f(food_id)
    WHERE r.recipe_release_id IS NULL AND r.price_data_release_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM recipe_data_releases WHERE active)
)
"""


def materialize_quote(row) -> PriceQuote:
    data = {**row['quote_payload'], 'recipe_release_id': row['recipe_release_id'],
            'price_data_release_id': row['price_data_release_id'],
            'snapshot_id': row['id'], 'usage_status': row['usage_status']}
    if row.get('food_id') is not None:
        # A source quote may group several legacy IDs. Expose only the binding
        # actually verified for this read; retain all source IDs in raw storage.
        data['food_ids'] = [row['food_id']]
    if row['usage_status'] == 'historical_requires_refresh' and data['status'] == 'success':
        data.update(status=QuoteStatus.STALE_PRICE, missing_reason='PRICE_REVALIDATION_REQUIRED',
                    price=None, selection_tier=SelectionTier.MISSING,
                    estimated_price=None, estimated_currency=None)
    return PriceQuote.model_validate(data)
