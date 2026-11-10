"""Run a file-only T02 review workbook recheck against public retail pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.source_adapters.price_models import Availability, IngredientQuery, MatchDecision
from app.source_adapters.price_scope import quote_unusable_reason
from app.source_adapters.product_matching import normalize
from app.source_adapters.pxmart import PxMartAdapter, candidate_from_raw, parse_products
from app.source_adapters.uni_prosperity import UniProsperityAdapter


PX = "shop.pxgo.com.tw"
UNI = "online.uni-prosperity.com.tw"
HOSTS = {
    PX, UNI, "pxbox.es.pxmart.com.tw", "www.pxmart.com.tw", "www.theeupantry.com",
    "www.wlscook.com.tw", "shopee.tw", "www.twfood.cc", "www.freshgo.com.tw",
    "ycbake.com.tw", "www.609.com.tw", "www.chezbixsausage.com", "shop.chefchouchou.com",
    "www.cross-country.com.tw", "city9.tw", "www.kayanoya.com.tw",
}
USER_AGENT = "overcooked-t02-price-research/1.0 (+academic project; low-rate)"
SKIP_NOTES = ("不計價", "不用錢", "不用特別列出", "自來水")
TRACKING = {"srsltid", "gad_source", "gad_campaignid", "gbraid", "gclid", "fbclid", "tblci", "cbs"}
CONFIRMED_RULES = Path(__file__).resolve().parents[1] / "config" / "t02_confirmed_recheck.json"
HEADERS = (
    "Food_ID", "食材", "缺價類別", "判定原因", "處理方式", "指定來源",
    "建議搜尋詞", "來源或商品網址", "包裝淨重（g）", "補充說明或換算依據",
)


@dataclass(frozen=True)
class ReviewItem:
    food_id: str
    name: str
    terms: list[str]
    url: str
    weight_hint: str
    note: str
    skip: bool


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


def load_review(raw: bytes) -> list[ReviewItem]:
    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    try:
        rows = book["待處理清單"].iter_rows(min_row=7, max_col=10, values_only=True)
        if next(rows, None) != HEADERS:
            raise ValueError("Unexpected T02 review workbook columns")
        result = []
        seen = set()
        for row in rows:
            if not any(value is not None for value in row):
                continue
            fid, name, _, _, method, _, keywords, url, weight, note = (
                str(value).strip() if value is not None else "" for value in row
            )
            if not re.fullmatch(r"FOOD\d{6}", fid) or fid in seen or not name:
                raise ValueError(f"Invalid or duplicate T02 ingredient: {fid}")
            seen.add(fid)
            terms = [re.sub(r"^改搜尋\s*", "", term.strip()) for term in re.split(r"[、，,；;]", keywords)]
            skip = any(marker in note for marker in SKIP_NOTES) or method == "不計價"
            result.append(ReviewItem(fid, name, list(dict.fromkeys(filter(None, terms))), url, weight, note, skip))
        if not result:
            raise ValueError("The T02 review workbook has no ingredient rows")
        return result
    finally:
        book.close()


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


def direct_product(pages: PublicPages, url: str, query: IngredientQuery) -> dict:
    requested = public_url(url)
    response = pages.get(requested)
    observed = datetime.now(timezone.utc)
    observation = {"via": "指定網址", "url": str(response.url), "at": observed.isoformat()}
    if response.status_code != 200:
        return dict(observation, reason=f"指定網址回傳 HTTP {response.status_code}")
    products = parse_products(response.text)
    if not products:
        return dict(observation, reason="指定網址未提供可辨識的商品資料，可能是搜尋頁、文章或需要互動")
    if len(products) > 1:
        return dict(observation, reason="同頁有多筆商品資料，需指定單一商品")
    product = products[0]
    offers = product.get("offers")
    if ((isinstance(offers, list) and len(offers) != 1)
        or (isinstance(offers, dict) and offers.get("@type") == "AggregateOffer")):
        return dict(observation, product=product.get("name"), reason="指定商品有多個報價或規格，需指定單一包裝")
    candidate = candidate_from_raw(query, {**product, "url": str(response.url)})
    price = candidate.price
    reason = _availability_reason(candidate, observed)
    if reason is not None:
        pass
    elif candidate.match_decision is not MatchDecision.MATCH:
        detail = "、".join(candidate.rejection_reasons or candidate.match_reasons)
        reason = f"有報價，指定商品與食材的對應仍需核對（{detail}）"
    elif candidate.package_unit == "ml":
        reason = "有報價，包裝以 mL 標示，缺同規格公克淨重"
    elif candidate.package_unit != "g" or not candidate.package_quantity:
        reason = "有報價，未取得可用的包裝公克數"
    elif candidate.weight_reliability.value not in {"exact", "nominal_tolerance"}:
        reason = "有報價，包裝重量僅為下限或未確認"
    return dict(observation, product=candidate.product_name, price=str(price) if price is not None and price > 0 else None,
                currency=candidate.currency, quantity=str(candidate.package_quantity) if candidate.package_quantity else None,
                unit=candidate.package_unit, reason=reason)


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


def recheck_confirmed(rule: ConfirmedRule, pages: PublicPages) -> dict:
    result = {"via": "行情估價" if rule.kind == "market_estimate" else "核准指定商品",
              "url": rule.url, "product": rule.product, "at": datetime.now(timezone.utc).isoformat(),
              "quantity": rule.quantity, "unit": rule.unit, "quote_kind": rule.kind}
    try:
        result = confirmed_observation(rule, pages, result)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if type(exc) in {httpx.RequestError, ValueError} else type(exc).__name__
        result = dict(result, reason=reason)
    status = "查無可用報價"
    if result["reason"] is None:
        status = "行情估價" if result.get("estimate") else "可按毫升計價" if rule.unit == "ml" else "可按克計價"
    return dict(asdict(rule), status=status, reason=result["reason"], picked=result, observations=[result])


def search_price(adapter: PxMartAdapter, query: IngredientQuery, pages: PublicPages) -> dict:
    quote = adapter.quote(query)
    reason = quote_unusable_reason(quote, datetime.now(timezone.utc))
    if reason == "UNIT_MISMATCH":
        reason = "有報價，缺同規格公克淨重" if quote.package_unit == "ml" else "包裝單位無法按克計價"
    elif reason == "source request failed: RequestError":
        reason = pages.denial_reason(quote.source_url) or reason
    price = quote.price if quote.price is not None else quote.estimated_price
    return dict(via=adapter.retailer, url=quote.product_url or quote.source_url, at=quote.queried_at.isoformat(),
                terms=quote.query_terms, product=quote.product_name or quote.estimated_product_name,
                price=str(price) if price is not None else None, currency=quote.currency or quote.estimated_currency,
                quantity=str(quote.package_quantity) if quote.package_quantity else None, unit=quote.package_unit,
                estimate=quote.estimated_price is not None, reason=reason, rejected=quote.rejection_summary)


def recheck(item: ReviewItem, pages: PublicPages) -> dict:
    record = asdict(item)
    if item.skip:
        return dict(record, status="不計價", reason=item.note or "表內標記不計價", observations=[])
    query = IngredientQuery(canonical_name=item.name, food_ids=[item.food_id], preferred_search_terms=item.terms)
    observations = []
    if item.url:
        try:
            observations.append(direct_product(pages, item.url, query))
        except (httpx.HTTPError, ValueError) as exc:
            message = str(exc) if type(exc) in {httpx.RequestError, ValueError} else type(exc).__name__
            observations.append(dict(via="指定網址", url=item.url, reason=message))
    for factory in (PxMartAdapter, UniProsperityAdapter):
        if observations and observations[-1]["reason"] is None:
            break
        with factory(client=pages, retries=0) as adapter:
            observations.append(search_price(adapter, query, pages))
    return summarize(record, observations)


def summarize(record: dict, observations: list[dict]) -> dict:
    if record["name"] == "肉絲" and "豬腿肉絲" in record["terms"]:
        for item in observations:
            if item["reason"] is None and "豬腿肉絲" not in normalize(item.get("product") or ""):
                item["reason"] = "有報價，但商品未確認為指定的豬腿肉絲"
    valid = next((item for item in observations if item["reason"] is None), None)
    # Show the user-specified product first among priced observations needing review.
    priced = next((item for item in observations if item.get("price") and not item.get("estimate")
                   and not item.get("disqualified")), None)
    estimate = next((item for item in observations if item.get("estimate")), None)
    picked = valid or priced or estimate or observations[0]
    return dict(record, status="可按克計價" if valid else "有價待確認" if priced else "僅有估價" if estimate else "查無報價",
                reason=picked["reason"], picked=picked, observations=observations)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--workbook", type=Path)
    source.add_argument("--confirmed", action="store_true", help="Use the versioned 79 confirmed pricing rules")
    parser.add_argument("--output", type=Path, required=True, help="New JSONL review results file")
    parser.add_argument("--resume", action="store_true", help="Continue an interrupted run against the identical workbook")
    args = parser.parse_args()
    path = CONFIRMED_RULES if args.confirmed else args.workbook
    raw = path.read_bytes()
    if args.confirmed:
        data, items = load_confirmed(raw)
        manifest = {"rules": path.name, "workbook": data["workbook"], "workbook_sha256": data["workbook_sha256"]}
        check = recheck_confirmed
    else:
        items = load_review(raw)
        manifest = {"workbook": path.name}
        check = recheck
    manifest.update(sha256=hashlib.sha256(raw).hexdigest(), items=len(items))
    completed = set()
    if args.resume:
        metadata, *saved = (json.loads(line) for line in args.output.read_text(encoding="utf-8").splitlines())
        completed = {row["food_id"] for row in saved}
        if metadata != manifest or len(completed) != len(saved) or not completed <= {item.food_id for item in items}:
            parser.error("Checkpoint does not match the workbook and its Food_IDs")
    pending = [item for item in items if item.food_id not in completed]
    with args.output.open("a" if args.resume else "x", encoding="utf-8") as out, httpx.Client(
        timeout=12, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
    ) as client:
        if not args.resume:
            out.write(json.dumps(manifest, ensure_ascii=False) + "\n")
            out.flush()
        pages = PublicPages(client)
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, result in enumerate(pool.map(lambda item: check(item, pages), pending), len(completed) + 1):
                out.write(json.dumps(result, ensure_ascii=False) + "\n")
                out.flush()
                print(f"[{i}/{len(items)}] {result['food_id']} {result['name']} {result['status']}", flush=True)


if __name__ == "__main__":
    main()
