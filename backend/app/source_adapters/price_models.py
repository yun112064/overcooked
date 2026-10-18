"""Contracts shared by retail price source adapters.

Prices are deliberately optional: an unavailable price is not the numeric value
zero.  The models also retain rejected candidates so matching decisions remain
auditable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator


class QuoteStatus(StrEnum):
    SUCCESS = "success"
    REVIEW_REQUIRED = "review_required"
    NOT_FOUND = "not_found"
    MISSING_PRICE = "missing_price"
    UNUSABLE_PACKAGE = "unusable_package"
    STALE_PRICE = "stale_price"
    OUT_OF_STOCK = "out_of_stock"
    TIMEOUT = "timeout"
    SOURCE_ERROR = "source_error"
    SCHEMA_CHANGED = "schema_changed"


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class MatchDecision(StrEnum):
    MATCH = "match"
    REVIEW = "review"
    REJECT = "reject"


class SelectionTier(StrEnum):
    AUTO_ADOPTED = "auto_adopted"
    ESTIMATE = "estimate"
    MISSING = "missing"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WeightReliability(StrEnum):
    EXACT = "exact"
    NOMINAL_TOLERANCE = "nominal_tolerance"
    LOWER_BOUND = "lower_bound"
    UNKNOWN = "unknown"


class IngredientQuery(BaseModel):
    """One canonical workbook ingredient and its non-expanding match hints."""

    canonical_name: str
    food_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    mapping_missing_reason: str | None = None

    @field_validator("canonical_name")
    @classmethod
    def canonical_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("canonical_name must not be blank")
        return value


class ProductCandidate(BaseModel):
    product_id: str | None = None
    product_name: str
    brand: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    package_quantity: Decimal | None = None
    package_unit: str | None = None
    package_description: str | None = None
    package_total_quantity: Decimal | None = None
    package_item_count: int | None = None
    package_per_item_quantity: Decimal | None = None
    package_tolerance: Decimal | None = None
    edible_quantity: Decimal | None = None
    weight_basis: str | None = None
    weight_reliability: WeightReliability = WeightReliability.UNKNOWN
    product_url: str | None = None
    availability: Availability = Availability.UNKNOWN
    price_kind: str = "displayed"
    unit_price_per_100: Decimal | None = None
    unit_price_per_1000: Decimal | None = None
    promotion_description: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    match_decision: MatchDecision
    ingredient_type: str = "other"
    score: int = 0
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    match_reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("price")
    @classmethod
    def price_cannot_be_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("price cannot be negative")
        return value


class PriceQuote(BaseModel):
    """T02 PriceQuote, including traceability and explicit missing data."""

    canonical_name: str
    food_ids: list[str] = Field(default_factory=list)
    retailer: str = "PX Mart"
    source: str = "PX Mart Hour Arrive public search"
    source_url: str
    queried_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    region_condition: str | None = None
    store_condition: str | None = None
    status: QuoteStatus
    missing_reason: str | None = None
    selection_tier: SelectionTier = SelectionTier.MISSING
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    selection_policy_version: str = "pxmart-selection-v2"
    selection_method: str | None = None
    selection_reasons: list[str] = Field(default_factory=list)
    product_id: str | None = None
    product_name: str | None = None
    brand: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    package_quantity: Decimal | None = None
    package_unit: str | None = None
    package_description: str | None = None
    package_total_quantity: Decimal | None = None
    package_item_count: int | None = None
    package_per_item_quantity: Decimal | None = None
    package_tolerance: Decimal | None = None
    edible_quantity: Decimal | None = None
    weight_basis: str | None = None
    weight_reliability: WeightReliability = WeightReliability.UNKNOWN
    product_url: str | None = None
    availability: Availability = Availability.UNKNOWN
    price_kind: str | None = None
    unit_price_per_100: Decimal | None = None
    unit_price_per_1000: Decimal | None = None
    promotion_description: str | None = None
    estimated_product_id: str | None = None
    estimated_product_name: str | None = None
    estimated_price: Decimal | None = None
    estimated_currency: str | None = None
    candidates: list[ProductCandidate] = Field(default_factory=list)
    raw_candidate_count: int = 0
    retained_candidate_count: int = 0
    rejected_candidate_count: int = 0
    rejection_summary: dict[str, int] = Field(default_factory=dict)
    query_terms: list[str] = Field(default_factory=list)
    source_request_count: int = 0

    @model_validator(mode="after")
    def missing_values_must_be_explicit(self) -> "PriceQuote":
        if self.status is not QuoteStatus.SUCCESS and not self.missing_reason:
            raise ValueError("non-success quote requires missing_reason")
        if self.status is not QuoteStatus.SUCCESS and self.price is not None:
            raise ValueError("non-success quote cannot expose a selected price")
        if self.status is QuoteStatus.SUCCESS:
            if self.selection_tier is not SelectionTier.AUTO_ADOPTED:
                raise ValueError("success quote must be auto adopted")
            if self.confidence is not ConfidenceLevel.HIGH:
                raise ValueError("auto-adopted quote must have high confidence")
            if self.price is None or self.package_quantity is None or not self.package_unit:
                raise ValueError("success quote requires price and package quantity/unit")
            if self.price <= 0:
                raise ValueError("success quote requires a positive retail price")
            if self.availability is not Availability.IN_STOCK:
                raise ValueError("success quote must be in stock")
        if self.selection_tier is SelectionTier.ESTIMATE:
            if self.status is not QuoteStatus.REVIEW_REQUIRED:
                raise ValueError("estimate must use review_required status")
            if self.estimated_price is None:
                raise ValueError("estimate requires estimated_price")
        if self.selection_tier is SelectionTier.MISSING and self.estimated_price is not None:
            raise ValueError("missing quote cannot expose an estimated price")
        return self


class RetailPriceAdapter(Protocol):
    """Reusable backend boundary for future retailers."""

    retailer: str

    def quote(
        self,
        query: IngredientQuery,
        *,
        preferred_product_id: str | None = None,
        representative_product_url: str | None = None,
        force_reassessment: bool = False,
    ) -> PriceQuote: ...
