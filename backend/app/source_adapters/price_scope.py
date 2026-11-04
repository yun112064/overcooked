"""Version-aware T02 reads. Import time never makes a historical quote current."""
from datetime import datetime, timedelta

from .price_models import PriceQuote, QuoteStatus, SelectionTier, WeightReliability

LIVE_QUOTE_MAX_AGE_WITHOUT_SOURCE_EXPIRY = timedelta(hours=48)


def quote_unusable_reason(quote: PriceQuote | None, now: datetime) -> str | None:
    """Return why a quote cannot be used to price a recipe measured in grams."""
    if quote is None:
        return "NO_PRICE_DATA"
    if quote.status is not QuoteStatus.SUCCESS or quote.price is None:
        return quote.missing_reason or "NO_PRICE_DATA"
    if quote.package_unit != "g":
        return "UNIT_MISMATCH"
    if not quote.package_quantity or quote.package_quantity <= 0:
        return "INVALID_PACKAGE"
    if quote.valid_until is not None and quote.valid_until < now:
        return "PRICE_EXPIRED"
    if quote.currency != "TWD":
        return "CURRENCY_MISMATCH"
    return None

# Match only current-format retail snapshots and the exact retailer package.
PACKAGE_MASS_JOIN = """
LEFT JOIN price_package_mass_evidence m ON r.recipe_release_id IS NOT NULL
 AND r.price_data_release_id IS NULL AND s.status = 'success' AND s.package_unit = 'ml'
 AND m.provider = s.provider AND m.product_id = s.product_id
 AND m.product_name = s.product_name AND m.volume_ml = s.package_quantity
"""

# Unversioned rows are readable only in legacy fixtures without an active recipe
# release. Once T01 is published, both the run and food binding must match it.
SCOPED_QUOTES_SQL = f"""
WITH scoped_quotes AS (
    SELECT s.*, f.food_id, p.usage_status,
           r.recipe_release_id, r.price_data_release_id, m.net_mass_g, m.source_url AS mass_source_url
    FROM price_quote_snapshots s
    JOIN price_collection_runs r ON r.id = s.run_id
    JOIN recipe_data_releases a ON a.release_id = r.recipe_release_id AND a.active
    JOIN price_snapshot_foods f ON f.snapshot_id = s.id AND f.recipe_release_id = a.release_id
    LEFT JOIN price_data_releases p ON p.release_id = r.price_data_release_id
    {PACKAGE_MASS_JOIN}
    WHERE r.price_data_release_id IS NULL
       OR (p.active AND p.recipe_release_id = a.release_id)
    UNION ALL
    SELECT s.*, f.food_id, NULL::varchar AS usage_status,
           r.recipe_release_id, r.price_data_release_id, NULL::numeric, NULL::text
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
    quote = PriceQuote.model_validate(data)
    mass = row.get('net_mass_g')
    if quote.status is QuoteStatus.SUCCESS and quote.package_unit == 'ml' and mass is not None:
        quote = quote.model_copy(update={
            'package_quantity': mass,
            'package_unit': 'g',
            'package_total_quantity': mass,
            'package_per_item_quantity': mass if quote.package_item_count == 1 else None,
            'unit_price_per_100': quote.price * 100 / mass,
            'unit_price_per_1000': quote.price * 1000 / mass,
            'weight_basis': 'manufacturer_net_mass',
            'weight_reliability': WeightReliability.EXACT,
            'selection_reasons': [*quote.selection_reasons,
                                  f'Manufacturer net mass for {quote.package_quantity} ml retailer package '
                                  f'({row["mass_source_url"]})'],
        })
    if (quote.status is QuoteStatus.SUCCESS and quote.recipe_release_id is not None
            and quote.price_data_release_id is None and quote.valid_until is None):
        # Keep the retailer's raw validity untouched. T13 uses a local freshness window
        # for live observations whose source supplied no expiry.
        quote = quote.model_copy(update={
            'valid_until': quote.queried_at + LIVE_QUOTE_MAX_AGE_WITHOUT_SOURCE_EXPIRY,
        })
    return quote
