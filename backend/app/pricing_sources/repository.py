from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pricing_sources.quotes import RetailPriceQuote


LATEST_VALID_QUOTE_SQL = text(
    """
    SELECT p.product_id, p.source_product_id, p.product_name, p.retailer,
           p.package_weight_g, p.package_description, p.source_url,
           s.package_price, s.currency, s.source_method, s.observed_at,
           s.valid_until, s.region, s.promotion_text
      FROM retail_product_ingredients AS mapping
      JOIN retail_products AS p ON p.product_id = mapping.product_id
      JOIN retail_price_snapshots AS s ON s.product_id = p.product_id
     WHERE mapping.ingredient_id = :ingredient_id
       AND mapping.is_verified = TRUE
       AND p.is_active = TRUE
       AND p.package_weight_g IS NOT NULL
       AND p.package_weight_g > 0
       AND s.package_price > 0
       AND (s.valid_until IS NULL OR s.valid_until >= :now)
     ORDER BY (s.package_price / p.package_weight_g) ASC,
              s.observed_at DESC,
              s.snapshot_id DESC
     LIMIT 1
    """
)


class RetailPricingRepository:
    """Read-only access to verified, non-expired Ticket 02 price snapshots."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def latest_valid_quote(
        self, ingredient_id: str, *, now: datetime
    ) -> RetailPriceQuote | None:
        row = self.db.execute(
            LATEST_VALID_QUOTE_SQL,
            {"ingredient_id": ingredient_id, "now": now},
        ).mappings().first()
        if row is None:
            return None
        return RetailPriceQuote(
            ingredient_id=ingredient_id,
            product_id=row["product_id"],
            source_product_id=row["source_product_id"],
            product_name=row["product_name"],
            retailer=row["retailer"],
            package_weight_g=Decimal(row["package_weight_g"]),
            package_description=row["package_description"],
            package_price=Decimal(row["package_price"]),
            currency=row["currency"],
            source_url=row["source_url"],
            source_method=row["source_method"],
            observed_at=row["observed_at"],
            valid_until=row["valid_until"],
            region=row["region"],
            promotion_text=row["promotion_text"],
        )
