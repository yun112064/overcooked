"""Run a file-only T02 review workbook recheck against public retail pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time  # noqa: F401  (tests patch recheck.time.sleep)
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.source_adapters.confirmed_products import (
    USER_AGENT, ConfirmedRule, PublicPages, _availability_reason, confirmed_observation, load_confirmed, public_url,
)
from app.source_adapters.price_models import IngredientQuery, MatchDecision
from app.source_adapters.price_scope import quote_unusable_reason
from app.source_adapters.product_matching import normalize
from app.source_adapters.pxmart import PxMartAdapter, candidate_from_raw, parse_products
from app.source_adapters.uni_prosperity import UniProsperityAdapter


SKIP_NOTES = ("不計價", "不用錢", "不用特別列出", "自來水")
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
