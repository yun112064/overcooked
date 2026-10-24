from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from backend.app.pricing_sources.common import (
    RetailRecord,
    SourceAccessBlocked,
    USER_AGENT,
    fetch_text,
    has_variable_weight,
    json_ld_documents,
    open_with_retry,
    parse_weight_g,
    product_matches,
)


API_URL = "https://api-pxbox.es.pxmart.com.tw/app/3.0/spu/single_search"
PRODUCT_URL = "https://pxbox.es.pxmart.com.tw/product/{}"
HOUR_ARRIVE_HOST = "shop.pxgo.com.tw"
HOUR_ARRIVE_PATH = "/hourArrive/goods/"
KNOWN_VARIABLE_WEIGHT_PRODUCT_IDS = {"431200"}


def _category_is_applicable(item: dict, terms: list[str]) -> bool:
    paths = [
        ">".join(
            str(category.get(key) or "")
            for key in ("d1_category_name", "d2_category_name", "d3_category_name")
        )
        for category in item.get("product_categories") or []
    ]
    if not any(path.startswith(("食品/料理>", "飲料/沖調>")) for path in paths):
        return False
    combined = "|".join(paths)
    canonical_name = terms[0] if terms else ""
    if any(category in combined for category in ("南北乾貨", "乾燥蔬菜")) and "乾" not in canonical_name:
        return False
    rules = (
        (("餅乾", "糕餅", "休閒零食"), ("餅乾", "糕餅", "零食")),
        (("麵/泡麵",), ("麵", "麵條", "泡麵")),
        (("罐頭",), ("罐頭",)),
        (("沾拌醬/抹醬",), ("醬", "膏")),
        (("薑茶", "茶葉", "沖泡茶"), ("茶",)),
    )
    for blocked_categories, allowed_terms in rules:
        if any(category in combined for category in blocked_categories) and not any(
            allowed in term for allowed in allowed_terms for term in terms
        ):
            return False
    return True


def _detail_spec_values(item: dict) -> list[str]:
    values: list[str] = []
    for label in item.get("product_tag_labels") or []:
        if label.get("spec_name") not in {"規格/容量", "商品規格", "內容物說明"}:
            continue
        values.extend(
            str(tag.get("tag_name") or "").strip()
            for tag in label.get("tags") or []
            if str(tag.get("tag_name") or "").strip()
        )
    return values


def parse_product(item: dict, terms: list[str]) -> RetailRecord | None:
    name = str(item.get("product_name") or "").strip()
    record_id = str(item.get("id") or "")
    if (
        not name
        or record_id in KNOWN_VARIABLE_WEIGHT_PRODUCT_IDS
        or not _category_is_applicable(item, terms)
        or item.get("is_sold_out")
        or not product_matches(name, terms)
    ):
        return None
    detail_specs = _detail_spec_values(item)
    if any(has_variable_weight(spec) for spec in detail_specs):
        return None
    detail_weights = [
        parsed_weight
        for spec in detail_specs
        if (parsed_weight := parse_weight_g(spec)) is not None
    ]
    if len(set(detail_weights)) > 1:
        return None
    detail_weight = detail_weights[0] if detail_weights else None
    title_weight = parse_weight_g(name)
    has_capacity_only_detail = bool(detail_specs) and any(
        re.search(r"(?i)\d+(?:\.\d+)?\s*(?:ml|cc|l)(?![a-z])", spec)
        for spec in detail_specs
    ) and detail_weight is None
    if has_capacity_only_detail:
        return None
    # A disagreement may mean one source describes a single bag while the
    # price applies to a multipack. Do not silently choose either weight.
    if detail_weight is not None and title_weight is not None and detail_weight != title_weight:
        return None
    weight = detail_weight or title_weight
    manual_review_reason = None
    try:
        price = Decimal(str(item.get("sale_price")))
    except (InvalidOperation, TypeError):
        return None
    currency = str(item.get("price_currency") or item.get("currency") or "TWD").upper()
    if weight is None or price <= 0 or not record_id or currency != "TWD":
        return None
    promotion = ", ".join(str(tag) for tag in item.get("promotion_tags") or []) or None
    return RetailRecord(
        source_record_id=record_id,
        product_name=name,
        package_weight_g=weight,
        package_price=price,
        source_url=PRODUCT_URL.format(record_id),
        promotion_text=promotion,
        manual_review_reason=manual_review_reason,
    )


def fetch_products(keyword: str, terms: list[str], page_size: int = 20) -> tuple[int, list[RetailRecord]]:
    body = json.dumps(
        {
            "search_setting_type": 1,
            "keyword": keyword,
            "sort_type": 0,
            "page_index": 1,
            "page_size": page_size,
            "filters": [],
            "visitor_id": str(uuid4()),
            "user_info_id": None,
            "is_using_prod_es": True,
            "skip_brand_detection": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://pxbox.es.pxmart.com.tw",
            "Referer": "https://pxbox.es.pxmart.com.tw/search/result",
        },
    )
    with open_with_retry(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != "0000":
        message = str(payload.get("message", "unknown"))
        if any(word in message.casefold() for word in ("captcha", "驗證碼", "access denied")):
            raise SourceAccessBlocked("PXMart 要求人工驗證或拒絕存取，已停止來源")
        raise RuntimeError(f"PXMart API error: {message}")
    items = payload.get("data", {}).get("product_list") or []
    records = [record for item in items if (record := parse_product(item, terms)) is not None]
    return len(items), records


def _nuxt_value(registry: list[Any], value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(registry):
        return registry[value]
    return value


def _hour_arrive_store(html: str, product_name: str) -> tuple[str, str] | None:
    match = re.search(
        r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    try:
        registry = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(registry, list):
        return None

    stores: list[tuple[str, str]] = []
    product_shop_no: str | None = None
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        if {"shopNo", "shopName", "storeType"}.issubset(entry):
            shop_no = _nuxt_value(registry, entry.get("shopNo"))
            shop_name = _nuxt_value(registry, entry.get("shopName"))
            if isinstance(shop_no, str) and shop_no and isinstance(shop_name, str) and shop_name:
                stores.append((shop_no, shop_name))
        if {"goodsShowName", "shopNo", "sellPrice"}.issubset(entry):
            goods_name = _nuxt_value(registry, entry.get("goodsShowName"))
            shop_no = _nuxt_value(registry, entry.get("shopNo"))
            if goods_name == product_name and isinstance(shop_no, str) and shop_no:
                product_shop_no = shop_no

    if product_shop_no is None:
        return None
    return next((store for store in stores if store[0] == product_shop_no), None)


def parse_hour_arrive_page(html: str, source_url: str, terms: list[str]) -> RetailRecord | None:
    parsed_url = urlparse(source_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != HOUR_ARRIVE_HOST
        or not parsed_url.path.startswith(HOUR_ARRIVE_PATH)
    ):
        return None
    expected_sku = parsed_url.path.removeprefix(HOUR_ARRIVE_PATH).strip("/")
    if not expected_sku:
        return None

    for document in json_ld_documents(html):
        documents = document if isinstance(document, list) else [document]
        for product in documents:
            if not isinstance(product, dict) or product.get("@type") != "Product":
                continue
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                continue
            name = str(product.get("name") or "").strip()
            sku = str(product.get("sku") or "").strip()
            availability = str(offers.get("availability") or "")
            currency = str(offers.get("priceCurrency") or "").upper()
            offer_url = str(offers.get("url") or "").strip()
            if (
                sku != expected_sku
                or offer_url.rstrip("/") != source_url.rstrip("/")
                or currency != "TWD"
                or not availability.rstrip("/").endswith("/InStock")
                or not product_matches(name, terms)
                or "約" in name
                or any(word in name for word in ("蔥油", "蔥酥", "乾燥", "脫水", "冷凍", "調味"))
            ):
                continue
            weight = parse_weight_g(name)
            store = _hour_arrive_store(html, name)
            try:
                price = Decimal(str(offers.get("price")))
            except (InvalidOperation, TypeError):
                continue
            if weight is None or price <= 0 or store is None:
                continue
            shop_no, shop_name = store
            return RetailRecord(
                source_record_id=f"hour_arrive:{sku}",
                product_name=name,
                package_weight_g=weight,
                package_price=price,
                source_url=source_url,
                promotion_text="全聯小時達公開商品頁；價格與庫存依配送地址及門市而異",
                region=f"全聯小時達；門市：{shop_name}（{shop_no}）；非所有配送地址適用",
                source_method="public_page",
            )
    return None


def fetch_hour_arrive_product(source_url: str, terms: list[str]) -> tuple[int, list[RetailRecord]]:
    html = fetch_text(source_url)
    record = parse_hour_arrive_page(html, source_url, terms)
    return 1, [record] if record is not None else []
