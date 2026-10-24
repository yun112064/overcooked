from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class RetailPriceQuote:
    """A traceable retail package quote selected from Ticket 02 data."""

    ingredient_id: str
    product_id: int
    source_product_id: str
    product_name: str
    retailer: str
    package_weight_g: Decimal
    package_description: str | None
    package_price: Decimal
    currency: str
    source_url: str | None
    source_method: str
    observed_at: datetime
    valid_until: datetime | None
    region: str | None
    promotion_text: str | None
