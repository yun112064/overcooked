"""Version-aware T02 reads. Import time never makes a historical quote current."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .price_models import PriceQuote, QuoteStatus, SelectionTier, WeightReliability

LIVE_QUOTE_MAX_AGE_WITHOUT_SOURCE_EXPIRY = timedelta(hours=48)


def quote_unusable_reason(quote: PriceQuote | None, now: datetime, *, allow_volume: bool = False) -> str | None:
    """Validate a quote, retaining gram-only behavior unless volume is requested."""
    if quote is None:
        return "NO_PRICE_DATA"
    if quote.status is not QuoteStatus.SUCCESS or quote.price is None:
        return quote.missing_reason or "NO_PRICE_DATA"
    if quote.usage_status == "historical_requires_refresh":
        return "PRICE_REVALIDATION_REQUIRED"
    units = {"g", "ml", "毫升", "l", "公升"} if allow_volume else {"g"}
    if (quote.package_unit or "").casefold() not in units:
        return "UNIT_MISMATCH"
    if not quote.package_quantity or quote.package_quantity <= 0:
        return "INVALID_PACKAGE"
    if quote.valid_until is not None and quote.valid_until < now:
        return "PRICE_EXPIRED"
    if quote.queried_at > now or quote.valid_from is not None and quote.valid_from > now:
        return "PRICE_NOT_YET_VALID"
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


# Select the latest raw success per provider, or the latest snapshot when
# a food has never had a success. Historical materialization happens afterwards.
LATEST_QUOTES_SQL = text(SCOPED_QUOTES_SQL + """
    , requested AS (
        SELECT * FROM scoped_quotes WHERE food_id IN :food_ids
    ), successes AS (
        SELECT DISTINCT ON (food_id, provider) * FROM requested
        WHERE status = 'success'
        ORDER BY food_id, provider, queried_at DESC, id DESC
    ), fallback AS (
        SELECT DISTINCT ON (food_id) * FROM requested q
        WHERE NOT EXISTS (SELECT 1 FROM successes s WHERE s.food_id = q.food_id)
        ORDER BY food_id, queried_at DESC, id DESC
    )
    SELECT * FROM successes UNION ALL SELECT * FROM fallback
    ORDER BY food_id, provider
""").bindparams(bindparam("food_ids", expanding=True))


def latest_quotes(db: Session, food_ids: Iterable[str], *, now: datetime) -> dict[str, PriceQuote]:
    """Read requested T02 quotes together, retaining unusable quotes for diagnostics."""
    ids = sorted(set(food_ids))
    if not ids:
        return {}
    grouped: dict[str, list[PriceQuote]] = {}
    for row in db.execute(LATEST_QUOTES_SQL, {"food_ids": ids}).mappings():
        grouped.setdefault(row["food_id"], []).append(materialize_quote(row))

    selected = {}
    for food_id, quotes in grouped.items():
        successes = [quote for quote in quotes if quote.status is QuoteStatus.SUCCESS]
        selected[food_id] = _choose(successes, now) if successes else max(quotes, key=lambda q: q.queried_at)
    return selected


def latest_quote(db: Session, food_id: str, *, now: datetime | None = None) -> PriceQuote | None:
    """Read the selected quote for one food, including its unusable status if any."""
    now = now or datetime.now(timezone.utc)
    return latest_quotes(db, [food_id], now=now).get(food_id)


PRIMARY_RETAILER = "PX Mart"


def _unit_price(quote: PriceQuote) -> Decimal:
    return quote.price / quote.package_quantity if quote.package_quantity else Decimal("Infinity")


def _choose(successes: list[PriceQuote], now: datetime) -> PriceQuote:
    """各來源最新成功報價中選一筆（規則見 latest_quote）。"""
    usable = [q for q in successes if quote_unusable_reason(q, now) is None]
    current = usable or [q for q in successes if q.valid_until is None or q.valid_until >= now]
    if not current:
        return max(successes, key=lambda q: q.queried_at)
    # 不比較不同幣別的數值大小。T13 以 TWD 做預算；有效 TWD 報價優先，
    # 只有外幣時保留最新原始報價，後續由成本驗證明確回報 CURRENCY_MISMATCH。
    local_currency = [q for q in current if q.currency == "TWD"]
    if local_currency:
        current = local_currency
    elif len({q.currency for q in current}) > 1:
        return max(current, key=lambda q: q.queried_at)
    ranked = sorted(current, key=lambda q: (q.package_unit != "g", _unit_price(q), q.retailer != PRIMARY_RETAILER))
    chosen = ranked[0]
    if len(ranked) == 1:
        return chosen
    others = ", ".join(f"{q.retailer} {q.price}/{q.package_quantity}{q.package_unit}" for q in ranked[1:])
    reason = (f"cross-source: {chosen.retailer} {chosen.price}/{chosen.package_quantity}"
              f"{chosen.package_unit} has the lowest unit price for a complete package (vs {others})")
    return chosen.model_copy(update={"selection_reasons": [*chosen.selection_reasons, reason]})


# 批次版：所有 FOOD 編號各來源最新一筆成功快照（一次查詢）
LATEST_SUCCESS_ALL_SQL = SCOPED_QUOTES_SQL + """
    SELECT DISTINCT ON (food_id, provider) * FROM scoped_quotes
    WHERE status = 'success'
    ORDER BY food_id, provider, queried_at DESC, id DESC
"""


def latest_success_quotes(db: Session, *, now: datetime) -> dict[str, PriceQuote]:
    """每個 FOOD 編號依 latest_quote 規則選出的成功報價（一次查詢）；從未成功的食材不列。
    供問卷預算下限估算使用。"""
    grouped: dict[str, list[PriceQuote]] = {}
    for row in db.execute(text(LATEST_SUCCESS_ALL_SQL)).mappings().all():
        quote = materialize_quote(row)
        if quote.status is QuoteStatus.SUCCESS:
            grouped.setdefault(row['food_id'], []).append(quote)
    return {food_id: _choose(quotes, now) for food_id, quotes in grouped.items()}
