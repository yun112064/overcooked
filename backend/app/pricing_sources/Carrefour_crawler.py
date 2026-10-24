from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
import time
from urllib.parse import quote, urljoin

from backend.app.pricing_sources.common import (
    RetailRecord,
    fetch_text,
    json_ld_documents,
    normalize_name,
    parse_weight_g,
    product_matches,
)


STORE_ORIGIN = "https://online.uni-prosperity.com.tw"
SEARCH_URL = f"{STORE_ORIGIN}/zh/search/?q={{}}"


class _ProductAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.variants: list[tuple[str, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        name = str(attributes.get("data-name") or "").strip()
        variant = str(attributes.get("data-variant") or "").strip()
        if name and variant:
            self.variants.append((name, variant))
        if (
            tag == "a"
            and attributes.get("data-pid")
            and name
            and attributes.get("href")
        ):
            self.urls.append(urljoin(STORE_ORIGIN, str(attributes["href"])))


def _product_urls(html: str) -> list[str]:
    for document in json_ld_documents(html):
        if document.get("@type") == "ItemList":
            urls = [
                item.get("url")
                for item in document.get("itemListElement", [])
                if item.get("url")
            ]
            if urls:
                return urls
    parser = _ProductAttributeParser()
    parser.feed(html)
    return list(dict.fromkeys(parser.urls))


def _variant_values(html: str, product_name: str) -> list[str]:
    parser = _ProductAttributeParser()
    parser.feed(html)
    return [
        variant
        for name, variant in parser.variants
        if normalize_name(name) == normalize_name(product_name)
    ]


def _variant_weight(html: str, product_name: str) -> Decimal | None:
    weights = {
        weight
        for variant in _variant_values(html, product_name)
        if (weight := parse_weight_g(variant)) is not None
    }
    return next(iter(weights)) if len(weights) == 1 else None


def parse_product_page(html: str, terms: list[str]) -> RetailRecord | None:
    for document in json_ld_documents(html):
        if document.get("@type") != "Product":
            continue
        name = str(document.get("name") or "").strip()
        offer = document.get("offers") or {}
        if not name or not product_matches(name, terms):
            return None
        if str(offer.get("availability", "")).rsplit("/", 1)[-1] != "InStock":
            return None
        title_weight = parse_weight_g(name)
        variants = _variant_values(html, name)
        detail_weight = _variant_weight(html, name)
        has_capacity_only_detail = bool(variants) and any(
            re.search(r"(?i)\d+(?:\.\d+)?\s*(?:ml|cc|l)(?![a-z])", variant)
            for variant in variants
        ) and detail_weight is None
        if has_capacity_only_detail:
            return None
        weight = detail_weight or title_weight
        manual_review_reason = None
        if (
            detail_weight is not None
            and title_weight is not None
            and detail_weight != title_weight
        ):
            manual_review_reason = "商品標題重量與詳細規格淨重不一致"
        try:
            price = Decimal(str(offer.get("price")))
        except (InvalidOperation, TypeError):
            return None
        if weight is None or price <= 0 or offer.get("priceCurrency") != "TWD":
            return None
        source_url = str(offer.get("url") or "")
        source_record_id = str(document.get("sku") or document.get("mpn") or "")
        if not source_url or not source_record_id:
            return None
        return RetailRecord(
            source_record_id=source_record_id,
            product_name=name,
            package_weight_g=weight,
            package_price=price,
            source_url=source_url,
            manual_review_reason=manual_review_reason,
        )
    return None


def fetch_products(
    keyword: str,
    terms: list[str],
    max_products: int = 5,
    request_interval_seconds: float = 1.0,
) -> tuple[int, list[RetailRecord]]:
    search_html = fetch_text(SEARCH_URL.format(quote(keyword)))
    urls = _product_urls(search_html)[:max_products]
    records = []
    for index, url in enumerate(urls):
        if index and request_interval_seconds > 0:
            time.sleep(request_interval_seconds)
        record = parse_product_page(fetch_text(url), terms)
        if record is not None:
            records.append(record)
    return len(urls), records
