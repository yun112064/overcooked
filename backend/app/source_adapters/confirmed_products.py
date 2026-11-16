"""User-confirmed T02 product pages (config/t02_confirmed_recheck.json).

Each rule names one public product page, its SKU and the approved package basis. Every
observation is a fresh fetch: robots.txt, per-host pacing (>= 2 s, one request at a time per
host), exact title/SKU/package checks and in-stock status must all pass, otherwise the rule
yields no price. Shared by scripts/recheck_t02_prices.py (file-only review) and the daily
collection's confirmed-product stage.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .price_models import (Availability, ConfidenceLevel, IngredientQuery, PriceQuote, QuoteStatus,
                           SelectionTier, WeightReliability)
from .pxmart import candidate_from_raw, parse_products

CONFIRMED_RULES = Path(__file__).resolve().parents[3] / "config" / "t02_confirmed_recheck.json"
PX = "shop.pxgo.com.tw"
UNI = "online.uni-prosperity.com.tw"
HOSTS = {
    PX, UNI, "pxbox.es.pxmart.com.tw", "www.pxmart.com.tw", "www.theeupantry.com",
    "www.wlscook.com.tw", "shopee.tw", "www.twfood.cc", "www.freshgo.com.tw",
    "ycbake.com.tw", "www.609.com.tw", "www.chezbixsausage.com", "shop.chefchouchou.com",
    "www.cross-country.com.tw", "city9.tw", "www.kayanoya.com.tw",
}
USER_AGENT = "overcooked-t02-price-research/1.0 (+academic project; low-rate)"
TRACKING = {"srsltid", "gad_source", "gad_campaignid", "gbraid", "gclid", "fbclid", "tblci", "cbs"}
@dataclass(frozen=True)
class ConfirmedRule:
    food_id: str
    name: str
    url: str
    product: str
    kind: str
    quantity: int
    unit: str
    basis: str
    sku: str | None = None
    package_g: int | None = None
    solid_g: int | None = None
    volume_ml: int | None = None
    checks: list[str] | None = None
    evidence_url: str | None = None
    variant_sku: str | None = None
    variant_url: str | None = None


def load_confirmed(raw: bytes) -> tuple[dict, list[ConfirmedRule]]:
    data = json.loads(raw)
    rules = [ConfirmedRule(**item) for item in data["rules"]]
    if data["version"] != 1 or len(rules) != 79 or len({r.food_id for r in rules}) != len(rules):
        raise ValueError("Unexpected T02 confirmed rule version or Food_ID count")
    for rule in rules:
        if (not re.fullmatch(r"FOOD\d{6}", rule.food_id) or not rule.name or not rule.product
            or type(rule.quantity) is not int or rule.quantity <= 0 or rule.unit not in {"g", "ml"}
            or rule.kind not in {"retail", "weighed", "market_estimate"}):
            raise ValueError(f"Invalid confirmed pricing rule: {rule.food_id}")
        public_url(rule.url)
        if rule.variant_url and urlsplit(public_url(rule.variant_url)).netloc != urlsplit(rule.url).netloc:
            raise ValueError(f"Unexpected variant host: {rule.food_id}")
        if rule.kind == "market_estimate" and (
            urlsplit(rule.url).hostname != "www.twfood.cc" or (rule.quantity, rule.unit) != (1000, "g")
        ):
            raise ValueError(f"Invalid market estimate rule: {rule.food_id}")
    return data, rules


def public_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS or p.netloc != p.hostname:
        raise ValueError("網址不在本批允許的公開來源")
    if p.hostname == PX and p.path.rstrip("/") == "/mweb" and p.fragment.startswith("/commodity-details?"):
        fields = dict(parse_qsl(p.fragment.partition("?")[2]))
        sku = [fields.get(key, "") for key in ("goodsId", "goodsNo", "goodsBarcode")]
        if fields.get("type") != "hourArrive" or not all(re.fullmatch(r"[0-9]{1,18}", value) for value in sku):
            raise ValueError("全聯手機商品網址缺少完整商品識別")
        return f"https://{PX}/hourArrive/goods/{'-'.join(sku)}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit((p.scheme, p.netloc, p.path or "/", urlencode(query), ""))


class PublicPages:
    """Shared per-host pacing, robots policy, and circuit breaker for one run."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self.locks = {host: Lock() for host in HOSTS}
        self.last: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser] = {}
        self.blocked: dict[str, str] = {}
        self.denied: dict[str, str] = {}
        self.cache: dict[str, httpx.Response] = {}

    def _request(self, host: str, url: str, delay: float) -> httpx.Response:
        if host in self.last:
            time.sleep(max(0, delay - (time.monotonic() - self.last[host])))
        try:
            response = self.client.get(url)
            if response.status_code in {401, 403, 429}:
                self.blocked[host] = f"來源回傳 HTTP {response.status_code}，本次停止請求該站"
            return response
        finally:
            self.last[host] = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        url = public_url(url)
        requested = url
        host = urlsplit(url).netloc
        with self.locks[host]:
            if url in self.cache:
                return self.cache[url]
            if host in self.blocked:
                raise httpx.RequestError(self.blocked[host])
            if host not in self.robots:
                response = self._request(host, f"https://{host}/robots.txt", 2)
                policy = RobotFileParser()
                if response.status_code == 200:
                    policy.parse(response.text.splitlines())
                elif response.status_code in {404, 410}:
                    policy.allow_all = True
                else:
                    self.blocked[host] = f"robots.txt 回傳 HTTP {response.status_code}，本次停止請求該站"
                    raise httpx.RequestError(self.blocked[host])
                self.robots[host] = policy
            policy = self.robots[host]
            for _ in range(3):
                if not policy.can_fetch(USER_AGENT.split("/")[0], url):
                    return self._deny(requested, "robots.txt 未允許這個網址")
                response = self._request(host, url, max(2, policy.crawl_delay(USER_AGENT.split("/")[0]) or 0))
                if not response.is_redirect:
                    if response.is_success:
                        self.cache[url] = response
                    return response
                destination = urljoin(url, response.headers.get("location", ""))
                if urlsplit(destination).netloc != host:
                    return self._deny(requested, "來源轉往其他網域，需人工確認")
                try:
                    url = public_url(destination)
                except ValueError:
                    return self._deny(requested, "來源轉址不符合公開 HTTPS 網址規則")
            return self._deny(requested, "來源重複轉址")

    def _deny(self, url: str, reason: str) -> httpx.Response:
        self.denied[url] = reason
        raise httpx.RequestError(reason)

    def denial_reason(self, url: str) -> str | None:
        host = urlsplit(url).netloc
        return self.denied.get(public_url(url)) or self.blocked.get(host)


def _availability_reason(candidate, observed: datetime) -> str | None:
    if candidate.price is None or candidate.price <= 0:
        return "指定商品頁未提供單一正數售價"
    if candidate.currency != "TWD":
        return "指定商品頁的幣別未確認為 TWD"
    if candidate.availability is not Availability.IN_STOCK:
        return "指定商品缺貨或未確認可購買"
    if ((candidate.valid_from and observed < candidate.valid_from)
        or (candidate.valid_until and candidate.valid_until < observed)):
        return "指定商品報價不在有效期間"
    return None


def _page_text(source: str) -> str:
    source = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", source, flags=re.I | re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", source)).split())


def _spec_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


_WEIGHT_RATE = re.compile(r"每\s*100\s*g\s*([0-9]+(?:\.[0-9]+)?)\s*元", re.I)
_MARKET_RATE = re.compile(r"預估零售價[:：]?\s*([0-9][0-9,.]*)\s*\(元/公斤\)")


def _variant_offer(pages: PublicPages, page: str, rule: ConfirmedRule) -> dict:
    if rule.variant_url:
        if unquote(public_url(rule.variant_url)) not in unquote(unescape(page)):
            raise ValueError("商品頁未連到核准的白玉麵規格")
        response = pages.get(rule.variant_url)
        if response.status_code != 200:
            raise ValueError(f"規格查詢回傳 HTTP {response.status_code}")
        detail = response.json().get("product", {})
        attributes = detail.get("variationAttributes", [])
        selected = [value.get("displayValue") for attribute in attributes if attribute.get("id") == "vStyle"
                    for value in attribute.get("values", []) if value.get("selected") is True]
        if selected != ["白玉麵"]:
            raise ValueError("規格回應未確認選中白玉麵")
        price = detail.get("price", {}).get("sales") or {}
        return {"price": price.get("value"), "priceCurrency": price.get("currency"),
                "availability": "InStock" if detail.get("available") is True else "OutOfStock"}
    match = re.search(r"app\.value\('product',\s*JSON\.parse\('((?:\\.|[^'\\])*)'\)", page)
    if match is None:
        raise ValueError("指定商品未提供可核對的包裝選項")
    encoded = match[1].replace("\\'", "'")
    product = json.loads(json.loads('"' + encoded + '"'))
    variants = [v for v in product.get("variations", []) if v.get("sku") == rule.variant_sku]
    if len(variants) != 1 or variants[0].get("fields_translations", {}).get("zh-hant") != ["25g小包裝"]:
        raise ValueError("指定商品未確認為 25 g 小包裝")
    variant = variants[0]
    sale = variant.get("price_sale") or {}
    price = sale if sale.get("cents", 0) > 0 else variant["price"]
    return {"price": price["dollars"], "priceCurrency": price["currency_iso"],
            "availability": "InStock" if variant.get("quantity", 0) > 0 else "OutOfStock"}


def confirmed_observation(rule: ConfirmedRule, pages: PublicPages, result: dict) -> dict:
    response = pages.get(rule.url)
    observed = datetime.now(timezone.utc)
    result.update(url=str(response.url), at=observed.isoformat())
    if response.status_code != 200:
        return dict(result, reason=f"指定網址回傳 HTTP {response.status_code}")
    if unquote(public_url(str(response.url))) != unquote(public_url(rule.url)):
        return dict(result, reason="指定網址轉往其他商品或規格")
    if rule.kind == "market_estimate":
        body = unicodedata.normalize("NFKC", _page_text(response.text))
        rate = _MARKET_RATE.search(body)
        amount = Decimal(rate[1].replace(",", "")) if rate else None
        if "豌豆-甜豌豆" not in body or amount is None or amount <= 0:
            return dict(result, reason="未找到 FL6 甜豌豆的正數預估零售價")
        return dict(result, estimate=True, estimated_price=str(amount), currency="TWD",
                    unit_price=str(amount / rule.quantity), reason=None)
    products = parse_products(response.text)
    if len(products) != 1:
        return dict(result, reason="指定網址未回傳唯一商品資料")
    product = products[0]
    title = str(product.get("name") or "")
    result["product"] = title
    title_key = lambda name: _spec_key(_WEIGHT_RATE.sub("每100g元", name) if rule.kind == "weighed" else name)
    if title_key(title) != title_key(rule.product) or (rule.sku and str(product.get("sku")) != rule.sku):
        return dict(result, reason="商品品名或 SKU 與核准版本不同")
    if rule.checks:
        body = _spec_key(_page_text(response.text))
        if any(_spec_key(check) not in body for check in rule.checks):
            return dict(result, reason="頁面包裝規格與核准版本不同或已無法辨識")
    offers = product.get("offers")
    if rule.variant_sku or rule.variant_url:
        offers = _variant_offer(pages, response.text, rule)
    elif isinstance(offers, list) and len(offers) == 1:
        offers = offers[0]
    if not isinstance(offers, dict) or offers.get("@type") == "AggregateOffer":
        return dict(result, reason="指定商品未回傳單一規格的報價")
    candidate = candidate_from_raw(
        IngredientQuery(canonical_name=rule.name, food_ids=[rule.food_id]),
        {**product, "offers": offers, "url": str(response.url)},
    )
    reason = _availability_reason(candidate, observed)
    if reason:
        return dict(result, reason=reason, availability=candidate.availability.value)
    if rule.kind == "weighed":
        rate = _WEIGHT_RATE.search(unicodedata.normalize("NFKC", title))
        if rate is None or Decimal(rate[1]) <= 0:
            return dict(result, reason="指定秤重商品未提供每 100 g 的售價")
        amount = Decimal(rate[1])
    else:
        amount = candidate.price
    return dict(result, reason=None, price=str(amount), currency=candidate.currency,
                unit_price=str(amount / rule.quantity), availability=candidate.availability.value)


# --- Daily collection stage -------------------------------------------------------------------

POLICY_VERSION = "t02-confirmed-products-v1"
PROVIDER = "confirmed"
RETAILERS = {PX: "PX Mart", "pxbox.es.pxmart.com.tw": "PX Mart", "www.pxmart.com.tw": "PX Mart", UNI: "萬家福"}


def load_confirmed_rules(path: str | Path = CONFIRMED_RULES) -> dict[str, ConfirmedRule]:
    """The versioned confirmed rules by Food_ID (validated by load_confirmed)."""
    _, rules = load_confirmed(Path(path).read_bytes())
    return {rule.food_id: rule for rule in rules}


class ConfirmedProductAdapter:
    """Quote an ingredient from its user-confirmed product page instead of a store search.

    A price is adopted only when the page passes every confirmed_observation check. The
    package basis (quantity/unit) is the one the user approved for the rule. Market estimates
    stay estimates; failed checks are recorded as missing, never as an older or guessed price.
    """

    provider = PROVIDER
    retailer = "User-confirmed product"

    def __init__(self, rules: dict[str, ConfirmedRule], *, timeout: float = 12,
                 client: httpx.Client | None = None) -> None:
        self.rules = rules
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})
        self.pages = PublicPages(self.client)

    def __enter__(self) -> "ConfirmedProductAdapter":
        return self

    def __exit__(self, *exc) -> None:
        if self._own_client:
            self.client.close()

    def quote(self, query: IngredientQuery, **_ignored) -> PriceQuote:
        matches = [self.rules[food_id] for food_id in query.food_ids if food_id in self.rules]
        if len(matches) != 1:
            return self._missing(query, None, QuoteStatus.NOT_FOUND,
                                 "no single confirmed product rule for this Food_ID")
        rule = matches[0]
        result = {"url": rule.url, "product": rule.product}
        try:
            result = confirmed_observation(rule, self.pages, result)
        except httpx.HTTPError as exc:
            return self._missing(query, rule, QuoteStatus.SOURCE_ERROR,
                                 f"confirmed product page request failed: {exc or type(exc).__name__}")
        except (ValueError, TypeError, KeyError) as exc:
            return self._missing(query, rule, QuoteStatus.SCHEMA_CHANGED,
                                 f"confirmed product page could not be verified: {exc or type(exc).__name__}")
        observed = datetime.fromisoformat(result["at"]) if result.get("at") else datetime.now(timezone.utc)
        if result["reason"] is not None:
            status = (QuoteStatus.OUT_OF_STOCK if result.get("availability") == Availability.OUT_OF_STOCK.value
                      else QuoteStatus.NOT_FOUND)
            return self._missing(query, rule, status, f"confirmed product check failed: {result['reason']}",
                                 observed, result.get("product"))
        quantity = Decimal(rule.quantity)
        common = self._common(query, rule, observed, result)
        if result.get("estimate"):
            return PriceQuote(**common, status=QuoteStatus.REVIEW_REQUIRED, selection_tier=SelectionTier.ESTIMATE,
                              missing_reason="estimated market price only; not a verified retail price",
                              estimated_product_name=result.get("product") or rule.product,
                              estimated_price=Decimal(result["estimated_price"]), estimated_currency="TWD")
        price = Decimal(result["price"])
        return PriceQuote(
            **common, status=QuoteStatus.SUCCESS, selection_tier=SelectionTier.AUTO_ADOPTED,
            confidence=ConfidenceLevel.HIGH, product_id=rule.sku or rule.url,
            product_name=result.get("product") or rule.product, price=price, currency=result["currency"],
            package_quantity=quantity, package_unit=rule.unit, package_total_quantity=quantity,
            package_description=f"{rule.quantity} {rule.unit} (user-confirmed basis)",
            weight_basis="user_confirmed_package", weight_reliability=WeightReliability.EXACT,
            product_url=result.get("url") or rule.url, availability=Availability.IN_STOCK, price_kind="displayed",
            unit_price_per_100=price * 100 / quantity, unit_price_per_1000=price * 1000 / quantity)

    def _common(self, query: IngredientQuery, rule: ConfirmedRule, observed: datetime, result: dict) -> dict:
        return dict(
            canonical_name=query.canonical_name, food_ids=list(query.food_ids),
            retailer=RETAILERS.get(urlsplit(rule.url).hostname or "", urlsplit(rule.url).hostname or self.retailer),
            source="User-confirmed product page (config/t02_confirmed_recheck.json)",
            source_url=rule.url, queried_at=observed,
            selection_policy_version=POLICY_VERSION, selection_method="confirmed_product_rule",
            selection_reasons=[f"{rule.kind} rule {rule.food_id} {rule.name}: {rule.basis}"],
            source_request_count=1)

    def _missing(self, query: IngredientQuery, rule: ConfirmedRule | None, status: QuoteStatus, reason: str,
                 observed: datetime | None = None, product: str | None = None) -> PriceQuote:
        observed = observed or datetime.now(timezone.utc)
        if rule is None:
            return PriceQuote(canonical_name=query.canonical_name, food_ids=list(query.food_ids),
                              retailer=self.retailer, source="User-confirmed product page",
                              source_url=str(CONFIRMED_RULES.name), queried_at=observed, status=status,
                              missing_reason=reason, selection_policy_version=POLICY_VERSION,
                              selection_method="confirmed_product_rule")
        return PriceQuote(**self._common(query, rule, observed, {}), status=status, missing_reason=reason,
                          product_name=product)
