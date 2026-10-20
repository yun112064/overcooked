"""萬家福線上購物（原家樂福線上購物）公開搜尋頁的保守查價 Adapter（決策 #25）。

只在全聯查無／不可用時使用。沿用 T02 的商品比對、包裝換算、可用性判斷與快照格式，
只保留事實資料（商品名稱、售價、包裝規格、網址、查詢時間、來源），不存圖片與描述。
遵守 robots.txt：每個請求送出前檢查網址；請求間隔至少 2 秒；可識別的 User-Agent。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from .price_models import IngredientQuery, PriceQuote, QuoteStatus, SelectionTier
from .product_matching import build_search_terms, parse_package_details, select_candidate
from .pxmart import HttpClient, PxMartAdapter, build_price_quote_from_candidates, candidate_from_raw

PROVIDER = "uniprosperity"
RETAILER = "萬家福"
SOURCE = "萬家福線上購物 public search"
BASE_URL = "https://online.uni-prosperity.com.tw"
SEARCH_URL = BASE_URL + "/zh/search/?q={query}"
STORE_CONDITION = (
    "No delivery address/store selected; the site's default warehouse catalog is shown and "
    "price/availability may vary by delivery area and checkout."
)
MIN_REQUEST_INTERVAL = 2.0
SEARCH_PAGE_MARKER = "search-result-page"
USER_AGENT = "overcooked-t02-price-research/1.0 (+academic project; low-rate)"

# robots.txt（2026-10-20 檢視）禁止的路徑與查詢參數；搜尋頁與商品頁未被禁止
_DISALLOWED_PATH_PARTS = (
    "/on/demandware.store/", "/filter/", "Search-UpdateGrid",
    "/cart", "/checkout", "/account", "/login", "/register", "/order", "/wishlist",
)
_DISALLOWED_QUERY_KEYS = ("cgid", "srule", "prefn", "prefv", "pmin", "pmax")


def is_allowed_url(url: str) -> bool:
    """只允許萬家福網域上 robots.txt 未禁止的網址。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != urlparse(BASE_URL).hostname:
        return False
    path = parsed.path.lower()
    if any(part.lower() in path for part in _DISALLOWED_PATH_PARTS):
        return False
    keys = [pair.split("=", 1)[0].lower() for pair in parsed.query.split("&") if pair]
    return not any(key.startswith(banned) for key in keys for banned in _DISALLOWED_QUERY_KEYS)


class _TileParser(HTMLParser):
    """擷取商品磚 <a class="gtm-product-alink"> 上的事實屬性。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tiles: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "a" and "gtm-product-alink" in values.get("class", "").split():
            self.tiles.append(values)


_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)(?![a-z])", re.IGNORECASE)


def parse_tiles(html_text: str) -> list[dict[str, object]]:
    """搜尋頁商品磚 → 與 T02 相同的 schema.org 風格原始資料（只含事實欄位）。"""
    parser = _TileParser()
    parser.feed(html_text)
    products: list[dict[str, object]] = []
    seen: set[str] = set()
    for tile in parser.tiles:
        pid = tile.get("data-pid", "").strip()
        name = tile.get("data-name", "").strip()
        if not pid or not name or pid in seen:
            continue
        seen.add(pid)
        href = tile.get("href", "").strip()
        url = urljoin(BASE_URL, href) if href else f"{BASE_URL}/zh/{pid}.html"
        if parse_package_details(name).total_quantity is None:
            weight = _WEIGHT.search(tile.get("data-variant", ""))
            if weight:  # 商品名稱沒寫重量時，取規格欄的克數／毫升數
                name = f"{name} {weight.group(1)}{weight.group(2).lower()}"
        segments = [unquote(s) for s in urlparse(url).path.split("/") if s]
        brand = segments[-2] if len(segments) >= 3 else None
        available = tile.get("data-ifavailable", "").strip().lower()
        products.append({
            "productID": pid,
            "name": name,
            "url": url,
            **({"brand": brand} if brand else {}),
            "offers": {
                "price": tile.get("data-price") or None,
                "priceCurrency": "TWD",
                "availability": "https://schema.org/" + (
                    "InStock" if available == "true" else "OutOfStock" if available == "false" else "Unknown"
                ),
            },
        })
    return products


class _ThrottledClient:
    """包住 HTTP 客戶端：任兩個請求之間至少間隔 min_interval 秒。"""

    def __init__(self, client: HttpClient, min_interval: float, sleep: Callable[[float], None],
                 clock: Callable[[], float]) -> None:
        self.inner = client
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self._last: float | None = None

    @property
    def headers(self):
        return self.inner.headers  # type: ignore[attr-defined]

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        try:
            return self.inner.get(url, **kwargs)
        finally:
            self._last = self.clock()

    def close(self) -> None:
        if hasattr(self.inner, "close"):
            self.inner.close()  # type: ignore[union-attr]


class UniProsperityAdapter(PxMartAdapter):
    retailer = RETAILER
    provider = PROVIDER
    # 正常頁面的前端字串就含「驗證碼」「captcha」；只以明確的封鎖頁字樣與 HTTP 狀態判斷
    block_markers = ("access denied", "robot check")

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        timeout: float = 15.0,
        retries: int = 1,
        retry_delay: float = 2.0,
        min_interval: float = MIN_REQUEST_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < MIN_REQUEST_INTERVAL:
            raise ValueError("min_interval must be at least 2 seconds")
        inner = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # 轉址可能落到禁止路徑；不跟隨
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
            },
        )
        super().__init__(client=inner, timeout=timeout, retries=retries, retry_delay=retry_delay, sleep=sleep)
        self._owns_client = client is None
        self.client = _ThrottledClient(inner, min_interval, sleep, clock)

    def _request(self, url: str):
        if not is_allowed_url(url):
            return None, "blocked by robots.txt policy; request not sent", 0
        return super()._request(url)

    def _relabel(self, quote_: PriceQuote) -> PriceQuote:
        return quote_.model_copy(update={"retailer": RETAILER, "source": SOURCE,
                                         "store_condition": STORE_CONDITION})

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
        seen: set[str] = set()
        request_count = 0
        last_url = SEARCH_URL.format(query=quote(query.canonical_name, safe=""))
        saw_search_page = False

        for term in terms:
            attempted.append(term)
            last_url = SEARCH_URL.format(query=quote(term, safe=""))
            response, error, attempts = self._request(last_url)
            request_count += attempts
            if error:
                status = QuoteStatus.TIMEOUT if "timed out" in error else QuoteStatus.SOURCE_ERROR
                return self._relabel(self._empty_quote(
                    query, last_url, status, error, query_terms=attempted, source_request_count=request_count))
            assert response is not None
            saw_search_page = saw_search_page or SEARCH_PAGE_MARKER in response.text
            for raw in parse_tiles(response.text):
                if raw["productID"] not in seen:
                    seen.add(str(raw["productID"]))
                    raw_products.append(raw)
            candidates = [self._candidate(query, raw) for raw in raw_products]
            if select_candidate(candidates, now=datetime.now(timezone.utc)).tier is SelectionTier.AUTO_ADOPTED:
                break

        if not raw_products:
            return self._relabel(self._empty_quote(
                query, last_url,
                QuoteStatus.NOT_FOUND if saw_search_page else QuoteStatus.SCHEMA_CHANGED,
                "source returned no products" if saw_search_page else (
                    "no product tiles or search-result marker found; source fields may have changed"),
                query_terms=attempted, source_request_count=request_count))
        return self._relabel(build_price_quote_from_candidates(
            query,
            [self._candidate(query, raw) for raw in raw_products],
            source_url=last_url,
            query_terms=attempted,
            source_request_count=request_count,
            preferred_product_id=preferred_product_id,
            force_reassessment=force_reassessment,
        ))

    @staticmethod
    def _candidate(query: IngredientQuery, raw: dict[str, object]):
        return candidate_from_raw(query, raw).model_copy(update={"product_id": str(raw["productID"])})
