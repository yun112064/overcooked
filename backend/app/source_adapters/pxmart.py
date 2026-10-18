"""Conservative adapter for the public PX Mart Hour Arrive search page."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, time as datetime_time, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.parse import quote, urlparse

import httpx

from .price_models import (
    Availability,
    ConfidenceLevel,
    IngredientQuery,
    PriceQuote,
    ProductCandidate,
    QuoteStatus,
    SelectionTier,
)
from .product_matching import (
    POLICY_VERSION,
    evaluate_match,
    parse_package_details,
    select_candidate,
)


SEARCH_URL = "https://shop.pxgo.com.tw/hourArrive/search/result?q={query}"
STORE_CONDITION = (
    "No delivery address/store selected; displayed catalog availability and "
    "price may vary by delivery area, store, order time, and checkout."
)
BLOCK_MARKERS = ("captcha", "驗證碼", "access denied", "robot check")
NO_RESULTS_MARKERS = ("找不到商品", "查無商品", "沒有符合")
class HttpClient(Protocol):
    def get(self, url: str, **kwargs: object) -> httpx.Response: ...


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self._capturing = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._capturing = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self.documents.append("".join(self._parts).strip())
            self._capturing = False
            self._parts = []


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(",", "").replace("NT$", "").strip()
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed >= 0 else None


def parse_package(text: str) -> tuple[Decimal | None, str | None, str | None]:
    """Backward-compatible tuple view over the richer package parser."""

    parsed = parse_package_details(text)
    return parsed.total_quantity, parsed.unit, parsed.description


def _availability(value: object) -> Availability:
    tail = str(value or "").rsplit("/", 1)[-1].lower()
    if tail == "instock":
        return Availability.IN_STOCK
    if tail in {"outofstock", "soldout", "discontinued"}:
        return Availability.OUT_OF_STOCK
    return Availability.UNKNOWN


def _offer_datetime(value: object, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed_date = datetime.fromisoformat(text).date()
            chosen_time = datetime_time.max if end_of_day else datetime_time.min
            return datetime.combine(parsed_date, chosen_time, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iter_product_documents(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _iter_product_documents(item)
    elif isinstance(value, dict):
        if str(value.get("@type", "")).lower() == "product":
            yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _iter_product_documents(graph)


def parse_products(html: str) -> list[dict[str, object]]:
    parser = _JsonLdParser()
    parser.feed(html)
    products: list[dict[str, object]] = []
    for document in parser.documents:
        if not document:
            continue
        try:
            value = json.loads(document)
        except json.JSONDecodeError:
            continue
        products.extend(_iter_product_documents(value))
    return products


def _candidate(query: IngredientQuery, raw: dict[str, object]) -> ProductCandidate:
    name = str(raw.get("name") or raw.get("description") or "").strip()
    offers = raw.get("offers")
    if isinstance(offers, list):
        offer = next((item for item in offers if isinstance(item, dict)), {})
    elif isinstance(offers, dict):
        offer = offers
    else:
        offer = {}
    price = _decimal(offer.get("price"))
    package = parse_package_details(name)
    product_url = str(raw.get("url") or "").strip() or None
    product_id = urlparse(product_url).path.rstrip("/").rsplit("/", 1)[-1] if product_url else None
    decision, ingredient_type, score, confidence, reasons, rejected = evaluate_match(
        query, name, package
    )
    availability = _availability(offer.get("availability"))
    if decision.value != "reject" and availability is Availability.IN_STOCK:
        score += 5
        reasons.append("source marks product in stock")
    if decision.value != "reject" and price is not None and price > 0:
        score += 5
        reasons.append("positive retail price present")
    currency = str(offer.get("priceCurrency") or "").upper() or None
    if decision.value != "reject" and currency == "TWD":
        score += 2
    if decision.value == "match" and score >= 80:
        confidence = ConfidenceLevel.HIGH
    return ProductCandidate(
        product_id=product_id or None,
        product_name=name or "(missing product name)",
        price=price,
        currency=currency,
        package_quantity=package.total_quantity,
        package_unit=package.unit,
        package_description=package.description,
        package_total_quantity=package.total_quantity,
        package_item_count=package.item_count,
        package_per_item_quantity=package.per_item_quantity,
        package_tolerance=package.tolerance,
        edible_quantity=package.edible_quantity,
        weight_basis=package.weight_basis,
        weight_reliability=package.reliability,
        product_url=product_url,
        availability=availability,
        valid_from=_offer_datetime(offer.get("validFrom")),
        valid_until=_offer_datetime(offer.get("priceValidUntil"), end_of_day=True),
        match_decision=decision,
        ingredient_type=ingredient_type,
        score=score,
        confidence=confidence,
        match_reasons=reasons,
        rejection_reasons=rejected,
        raw=raw,
    )


class PxMartAdapter:
    retailer = "PX Mart"

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        timeout: float = 15.0,
        retries: int = 1,
        retry_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 0 or retries > 3:
            raise ValueError("retries must be between 0 and 3")
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.sleep = sleep
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "overcooked-t02-price-research/1.0 (+academic project; low-rate)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
            },
        )

    def close(self) -> None:
        if self._owns_client and hasattr(self.client, "close"):
            self.client.close()  # type: ignore[union-attr]

    def __enter__(self) -> "PxMartAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _empty_quote(
        self,
        query: IngredientQuery,
        url: str,
        status: QuoteStatus,
        reason: str,
        *,
        candidates: list[ProductCandidate] | None = None,
    ) -> PriceQuote:
        return PriceQuote(
            canonical_name=query.canonical_name,
            food_ids=query.food_ids,
            source_url=url,
            queried_at=datetime.now(timezone.utc),
            region_condition="Taiwan; no precise delivery region selected",
            store_condition=STORE_CONDITION,
            status=status,
            missing_reason=reason,
            candidates=candidates or [],
        )

    def quote(
        self,
        query: IngredientQuery,
        *,
        preferred_product_id: str | None = None,
        representative_product_url: str | None = None,
        force_reassessment: bool = False,
    ) -> PriceQuote:
        url = SEARCH_URL.format(query=quote(query.canonical_name, safe=""))
        response: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(url)
            except httpx.TimeoutException:
                if attempt >= self.retries:
                    return self._empty_quote(query, url, QuoteStatus.TIMEOUT, "source request timed out")
                self.sleep(self.retry_delay * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.retries:
                    return self._empty_quote(
                        query, url, QuoteStatus.SOURCE_ERROR, f"source request failed: {type(exc).__name__}"
                    )
                self.sleep(self.retry_delay * (attempt + 1))
                continue

            body_lower = response.text.lower()
            if response.status_code in {401, 403, 429} or any(marker in body_lower for marker in BLOCK_MARKERS):
                return self._empty_quote(
                    query,
                    url,
                    QuoteStatus.SOURCE_ERROR,
                    f"source blocked or rate-limited the request (HTTP {response.status_code}); no bypass attempted",
                )
            if response.status_code >= 500 and attempt < self.retries:
                self.sleep(self.retry_delay * (attempt + 1))
                continue
            if response.status_code != 200:
                return self._empty_quote(
                    query, url, QuoteStatus.SOURCE_ERROR, f"source returned HTTP {response.status_code}"
                )
            break

        assert response is not None
        raw_products = parse_products(response.text)
        if not raw_products:
            if any(marker in response.text for marker in NO_RESULTS_MARKERS):
                return self._empty_quote(query, url, QuoteStatus.NOT_FOUND, "source returned no products")
            return self._empty_quote(
                query,
                url,
                QuoteStatus.SCHEMA_CHANGED,
                "no Schema.org Product JSON-LD found; source fields may have changed",
            )

        candidates = [_candidate(query, raw) for raw in raw_products]
        acceptable = [
            candidate for candidate in candidates
            if candidate.match_decision.value != "reject"
        ]
        if not acceptable:
            return self._empty_quote(
                query,
                url,
                QuoteStatus.NOT_FOUND,
                "products were returned but none passed ingredient/state matching",
                candidates=candidates,
            )

        in_stock = [candidate for candidate in acceptable if candidate.availability is Availability.IN_STOCK]
        if not in_stock:
            return self._empty_quote(
                query, url, QuoteStatus.OUT_OF_STOCK, "all matching candidates are out of stock or availability is unknown", candidates=candidates
            )
        priced = [candidate for candidate in in_stock if candidate.price is not None]
        if not priced:
            return self._empty_quote(
                query, url, QuoteStatus.MISSING_PRICE, "matching in-stock products do not expose a retail price", candidates=candidates
            )

        complete = [
            candidate for candidate in priced
            if candidate.package_total_quantity is not None and candidate.package_unit
        ]
        if not complete:
            return self._empty_quote(
                query,
                url,
                QuoteStatus.UNUSABLE_PACKAGE,
                "matching priced products have unparseable package quantity/unit",
                candidates=candidates,
            )

        now = datetime.now(timezone.utc)
        current = [
            candidate
            for candidate in complete
            if candidate.valid_until is None or candidate.valid_until >= now
        ]
        if not current:
            return self._empty_quote(
                query,
                url,
                QuoteStatus.STALE_PRICE,
                "all matching prices have expired according to source priceValidUntil",
                candidates=candidates,
            )

        outcome = select_candidate(
            current,
            preferred_product_id=(
                None if force_reassessment else preferred_product_id
            ),
            now=now,
        )
        if outcome.tier is SelectionTier.MISSING or outcome.selected is None:
            return self._empty_quote(
                query,
                url,
                QuoteStatus.NOT_FOUND,
                "; ".join(outcome.reasons),
                candidates=candidates,
            )

        selected = outcome.selected
        if outcome.tier is SelectionTier.ESTIMATE:
            return PriceQuote(
                canonical_name=query.canonical_name,
                food_ids=query.food_ids,
                source_url=url,
                queried_at=datetime.now(timezone.utc),
                region_condition="Taiwan; no precise delivery region selected",
                store_condition=STORE_CONDITION,
                status=QuoteStatus.REVIEW_REQUIRED,
                missing_reason=(
                    "estimated alternative only; not a verified price: "
                    + "; ".join(outcome.reasons)
                ),
                selection_tier=SelectionTier.ESTIMATE,
                confidence=selected.confidence,
                selection_policy_version=POLICY_VERSION,
                selection_method=outcome.method,
                selection_reasons=outcome.reasons,
                estimated_product_id=selected.product_id,
                estimated_product_name=selected.product_name,
                estimated_price=selected.price,
                estimated_currency=selected.currency,
                candidates=candidates,
            )

        return PriceQuote(
            canonical_name=query.canonical_name,
            food_ids=query.food_ids,
            source_url=url,
            queried_at=datetime.now(timezone.utc),
            valid_from=selected.valid_from,
            valid_until=selected.valid_until,
            region_condition="Taiwan; no precise delivery region selected",
            store_condition=STORE_CONDITION,
            status=QuoteStatus.SUCCESS,
            missing_reason=None,
            selection_tier=SelectionTier.AUTO_ADOPTED,
            confidence=selected.confidence,
            selection_policy_version=POLICY_VERSION,
            selection_method=outcome.method,
            selection_reasons=outcome.reasons,
            product_id=selected.product_id,
            product_name=selected.product_name,
            price=selected.price,
            currency=selected.currency,
            package_quantity=selected.package_quantity,
            package_unit=selected.package_unit,
            package_description=selected.package_description,
            package_total_quantity=selected.package_total_quantity,
            package_item_count=selected.package_item_count,
            package_per_item_quantity=selected.package_per_item_quantity,
            package_tolerance=selected.package_tolerance,
            edible_quantity=selected.edible_quantity,
            weight_basis=selected.weight_basis,
            weight_reliability=selected.weight_reliability,
            product_url=selected.product_url,
            availability=selected.availability,
            price_kind=selected.price_kind,
            promotion_description=selected.promotion_description,
            candidates=candidates,
        )
