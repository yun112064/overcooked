"""Conservative adapter for the public PX Mart Hour Arrive search page."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
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
    build_search_terms,
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


def _brand(query: IngredientQuery, raw: dict[str, object], name: str) -> str | None:
    value = raw.get("brand")
    if isinstance(value, dict):
        value = value.get("name")
    if value:
        return str(value).strip() or None
    positions = [
        name.find(term) for term in [query.canonical_name, *query.aliases]
        if term and name.find(term) > 0
    ]
    if positions:
        prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+$", "", name[:min(positions)]).strip()
        if 1 <= len(prefix) <= 12 and not prefix.isdigit():
            return prefix
    return None


def candidate_from_raw(query: IngredientQuery, raw: dict[str, object]) -> ProductCandidate:
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
    denominator = package.edible_quantity or package.total_quantity
    unit_price_per_100 = None
    unit_price_per_1000 = None
    if price is not None and denominator and denominator > 0 and package.unit in {"g", "ml"}:
        unit_price_per_100 = price * Decimal("100") / denominator
        unit_price_per_1000 = price * Decimal("1000") / denominator
    return ProductCandidate(
        product_id=product_id or None,
        product_name=name or "(missing product name)",
        brand=_brand(query, raw, name),
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
        unit_price_per_100=unit_price_per_100,
        unit_price_per_1000=unit_price_per_1000,
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


def _rejection_summary(candidates: list[ProductCandidate]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if candidate.match_decision.value != "reject":
            continue
        reason = candidate.rejection_reasons[0] if candidate.rejection_reasons else "rejected"
        counts[reason] += 1
    return dict(counts.most_common())


def _retain_valid_candidates(
    candidates: list[ProductCandidate],
    selected: ProductCandidate | None,
    limit: int = 5,
) -> list[ProductCandidate]:
    valid = [item for item in candidates if item.match_decision.value != "reject"]
    valid.sort(
        key=lambda item: (
            0 if selected and item.product_id == selected.product_id else 1,
            -item.score,
            0 if item.availability is Availability.IN_STOCK else 1,
            item.unit_price_per_100 or Decimal("Infinity"),
            item.product_id or item.product_name,
        )
    )
    retained: list[ProductCandidate] = []
    seen_brands: set[str] = set()
    if selected and selected in valid:
        retained.append(selected)
        if selected.brand:
            seen_brands.add(selected.brand.strip().lower())
    for candidate in valid:
        if candidate in retained:
            continue
        brand_key = (candidate.brand or "").strip().lower()
        if brand_key and brand_key not in seen_brands:
            retained.append(candidate)
            seen_brands.add(brand_key)
        if len(retained) >= limit:
            return retained
    for candidate in valid:
        if candidate not in retained:
            retained.append(candidate)
        if len(retained) >= limit:
            break
    return retained


def build_price_quote_from_candidates(
    query: IngredientQuery,
    candidates: list[ProductCandidate],
    *,
    source_url: str,
    queried_at: datetime | None = None,
    query_terms: list[str] | None = None,
    source_request_count: int = 0,
    preferred_product_id: str | None = None,
    force_reassessment: bool = False,
) -> PriceQuote:
    """Apply status/selection policy and retain at most five valid products."""

    queried_at = queried_at or datetime.now(timezone.utc)
    acceptable = [item for item in candidates if item.match_decision.value != "reject"]
    in_stock = [item for item in acceptable if item.availability is Availability.IN_STOCK]
    priced = [item for item in in_stock if item.price is not None and item.price > 0]
    complete = [
        item for item in priced
        if item.package_total_quantity is not None and item.package_unit
    ]
    current = [
        item for item in complete
        if item.valid_until is None or item.valid_until >= queried_at
    ]
    outcome = select_candidate(
        current,
        preferred_product_id=None if force_reassessment else preferred_product_id,
        now=queried_at,
    )
    selected = outcome.selected
    retained = _retain_valid_candidates(candidates, selected)
    common = {
        "canonical_name": query.canonical_name,
        "food_ids": query.food_ids,
        "source_url": source_url,
        "queried_at": queried_at,
        "region_condition": "Taiwan; no precise delivery region selected",
        "store_condition": STORE_CONDITION,
        "selection_policy_version": POLICY_VERSION,
        "candidates": retained,
        "raw_candidate_count": len(candidates),
        "retained_candidate_count": len(retained),
        "rejected_candidate_count": sum(
            item.match_decision.value == "reject" for item in candidates
        ),
        "rejection_summary": _rejection_summary(candidates),
        "query_terms": query_terms or [],
        "source_request_count": source_request_count,
    }
    if not acceptable:
        return PriceQuote(
            **common,
            status=QuoteStatus.NOT_FOUND,
            missing_reason="products were returned but none passed ingredient/state matching",
        )
    if not in_stock:
        return PriceQuote(
            **common,
            status=QuoteStatus.OUT_OF_STOCK,
            missing_reason="all matching candidates are out of stock or availability is unknown",
        )
    if not priced:
        return PriceQuote(
            **common,
            status=QuoteStatus.MISSING_PRICE,
            missing_reason="matching in-stock products do not expose a retail price",
        )
    if not complete:
        return PriceQuote(
            **common,
            status=QuoteStatus.UNUSABLE_PACKAGE,
            missing_reason="matching priced products have unparseable package quantity/unit",
        )
    if not current:
        return PriceQuote(
            **common,
            status=QuoteStatus.STALE_PRICE,
            missing_reason="all matching prices have expired according to source priceValidUntil",
        )
    if outcome.tier is SelectionTier.MISSING or selected is None:
        return PriceQuote(
            **common,
            status=QuoteStatus.NOT_FOUND,
            missing_reason="; ".join(outcome.reasons),
        )
    if outcome.tier is SelectionTier.ESTIMATE:
        return PriceQuote(
            **common,
            status=QuoteStatus.REVIEW_REQUIRED,
            missing_reason="estimated alternative only; not a verified price: " + "; ".join(outcome.reasons),
            selection_tier=SelectionTier.ESTIMATE,
            confidence=selected.confidence,
            selection_method=outcome.method,
            selection_reasons=outcome.reasons,
            estimated_product_id=selected.product_id,
            estimated_product_name=selected.product_name,
            estimated_price=selected.price,
            estimated_currency=selected.currency,
        )
    return PriceQuote(
        **common,
        status=QuoteStatus.SUCCESS,
        selection_tier=SelectionTier.AUTO_ADOPTED,
        confidence=selected.confidence,
        selection_method=outcome.method,
        selection_reasons=outcome.reasons,
        product_id=selected.product_id,
        product_name=selected.product_name,
        brand=selected.brand,
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
        unit_price_per_100=selected.unit_price_per_100,
        unit_price_per_1000=selected.unit_price_per_1000,
        promotion_description=selected.promotion_description,
        valid_from=selected.valid_from,
        valid_until=selected.valid_until,
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
        query_terms: list[str] | None = None,
        source_request_count: int = 0,
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
            raw_candidate_count=len(candidates or []),
            retained_candidate_count=len(candidates or []),
            query_terms=query_terms or [],
            source_request_count=source_request_count,
        )

    def _request(self, url: str) -> tuple[httpx.Response | None, str | None, int]:
        request_count = 0
        for attempt in range(self.retries + 1):
            request_count += 1
            try:
                response = self.client.get(url)
            except httpx.TimeoutException:
                if attempt >= self.retries:
                    return None, "source request timed out", request_count
                self.sleep(self.retry_delay * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.retries:
                    return None, f"source request failed: {type(exc).__name__}", request_count
                self.sleep(self.retry_delay * (attempt + 1))
                continue

            body_lower = response.text.lower()
            if response.status_code in {401, 403, 429} or any(
                marker in body_lower for marker in BLOCK_MARKERS
            ):
                return response, (
                    f"source blocked or rate-limited the request (HTTP {response.status_code}); "
                    "no bypass attempted"
                ), request_count
            if response.status_code >= 500 and attempt < self.retries:
                self.sleep(self.retry_delay * (attempt + 1))
                continue
            if response.status_code != 200:
                return response, f"source returned HTTP {response.status_code}", request_count
            return response, None, request_count
        return None, "source request failed", request_count

    def quote(
        self,
        query: IngredientQuery,
        *,
        preferred_product_id: str | None = None,
        representative_product_url: str | None = None,
        force_reassessment: bool = False,
    ) -> PriceQuote:
        terms = build_search_terms(query, max_terms=3)
        attempted: list[str] = []
        raw_products: list[dict[str, object]] = []
        seen_products: set[str] = set()
        request_count = 0
        last_url = SEARCH_URL.format(query=quote(query.canonical_name, safe=""))
        saw_no_results = False

        for term_index, term in enumerate(terms):
            if term_index:
                self.sleep(self.retry_delay)
            attempted.append(term)
            last_url = SEARCH_URL.format(query=quote(term, safe=""))
            response, error, attempts = self._request(last_url)
            request_count += attempts
            if error:
                status = QuoteStatus.TIMEOUT if "timed out" in error else QuoteStatus.SOURCE_ERROR
                return self._empty_quote(
                    query, last_url, status, error,
                    query_terms=attempted,
                    source_request_count=request_count,
                )
            assert response is not None
            products = parse_products(response.text)
            if not products:
                saw_no_results = saw_no_results or any(
                    marker in response.text for marker in NO_RESULTS_MARKERS
                )
                continue
            for raw in products:
                key = str(raw.get("url") or raw.get("name") or raw.get("description") or "")
                if key and key not in seen_products:
                    seen_products.add(key)
                    raw_products.append(raw)
            candidates = [candidate_from_raw(query, raw) for raw in raw_products]
            outcome = select_candidate(candidates, now=datetime.now(timezone.utc))
            if outcome.tier is SelectionTier.AUTO_ADOPTED:
                break

        if not raw_products:
            return self._empty_quote(
                query,
                last_url,
                QuoteStatus.NOT_FOUND if saw_no_results else QuoteStatus.SCHEMA_CHANGED,
                "source returned no products" if saw_no_results else (
                    "no Schema.org Product JSON-LD found; source fields may have changed"
                ),
                query_terms=attempted,
                source_request_count=request_count,
            )
        candidates = [candidate_from_raw(query, raw) for raw in raw_products]
        return build_price_quote_from_candidates(
            query,
            candidates,
            source_url=last_url,
            query_terms=attempted,
            source_request_count=request_count,
            preferred_product_id=preferred_product_id,
            force_reassessment=force_reassessment,
        )
