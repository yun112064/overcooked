import json
from dataclasses import replace
from io import BytesIO

import httpx
from openpyxl import Workbook

from app.source_adapters.price_models import IngredientQuery, MatchDecision
from app.source_adapters.product_matching import build_search_terms, evaluate_match, parse_package_details
from scripts import recheck_t02_prices as recheck


def test_workbook_search_terms_are_prioritized_but_do_not_override_product_matching():
    book = Workbook()
    sheet = book.active
    sheet.title = "待處理清單"
    for _ in range(6):
        sheet.append([])
    sheet.append(recheck.HEADERS)
    sheet.append(("FOOD000296", "白飯", None, None, None, None, "改搜尋 蓬萊米、米", None, None, None))
    sheet.append(("FOOD000494", "泡香菇的水", None, None, None, None, "水", None, None, "自來水"))
    output = BytesIO()
    book.save(output)
    rice, water = recheck.load_review(output.getvalue())
    query = IngredientQuery(canonical_name=rice.name, preferred_search_terms=rice.terms)
    assert build_search_terms(query) == ["蓬萊米", "米", "白飯"]
    assert evaluate_match(query, "蓬萊米 400g", parse_package_details("400g"))[0] is MatchDecision.REJECT
    result = recheck.recheck(water, None)
    assert result["status"] == "不計價"
    assert not result["observations"]


def test_review_product_preserves_price_when_volume_or_mismatch_requires_review(monkeypatch):
    monkeypatch.setattr(recheck.time, "sleep", lambda _: None)
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /blocked\n")
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://elsewhere.invalid/item"})
        product = {"@type": "Product", "name": "史雲生清雞湯 500mL", "offers": {
            "@type": "Offer", "price": "48", "priceCurrency": "TWD", "availability": "InStock",
        }}
        return httpx.Response(200, text='<script type="application/ld+json">' + json.dumps(product) + "</script>")

    url = "https://pxbox.es.pxmart.com.tw/product/19672?utm_source=tracking"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pages = recheck.PublicPages(client)
        result = recheck.direct_product(pages, url, IngredientQuery(canonical_name="史雲生清雞湯"))
        assert result["price"] == "48"
        assert result["unit"] == "ml"
        assert "公克淨重" in result["reason"]
        assert "utm_source" not in result["url"]
        recheck.direct_product(pages, url, IngredientQuery(canonical_name="鮪魚罐頭"))
        denied = "https://pxbox.es.pxmart.com.tw/redirect"
        try:
            pages.get(denied)
        except httpx.RequestError:
            assert "其他網域" in pages.denial_reason(denied)
        else:
            raise AssertionError("External redirect must not be followed")
    assert paths == ["/robots.txt", "/product/19672", "/redirect"]


def test_non_food_candidates_cannot_be_selected_by_an_exact_name_fragment():
    for food, product in (("啤酒", "附蓋啤酒壺1300ml"), ("四季豆", "四季豆 豆類種子 20g"),
                          ("紅酒", "紅酒裸麥法國 (每個約268g)")):
        query = IngredientQuery(canonical_name=food)
        assert evaluate_match(query, product, parse_package_details(product))[0] is MatchDecision.REJECT


def test_price_package_totals_count_first_multipack():
    info = parse_package_details("台灣嚴選帶皮雞腿丁(12包_250g/包)")
    assert info.total_quantity == 3000
    assert info.per_item_quantity == 250
    assert info.item_count == 12
    assert info.unit == "g"
    soda = parse_package_details("【可口可樂】(330mlx6罐)x4組")
    assert (soda.total_quantity, soda.item_count, soda.unit) == (7920, 24, "ml")


def test_pxmart_mobile_product_hash_preserves_exact_sku():
    url = ("https://shop.pxgo.com.tw/mweb/?utm_source=campaign"
           "#/commodity-details?type=hourArrive&goodsId=419087&goodsNo=310227&goodsBarcode=2631022714316")
    assert recheck.public_url(url) == "https://shop.pxgo.com.tw/hourArrive/goods/419087-310227-2631022714316"


def test_confirmed_rules_keep_exact_sku_and_calculation_basis(monkeypatch):
    monkeypatch.setattr(recheck.time, "sleep", lambda _: None)
    _, rules = recheck.load_confirmed(recheck.CONFIRMED_RULES.read_bytes())
    assert len(rules) == 79
    assert sum(rule.unit == "ml" for rule in rules) == 13
    stock = next(r for r in rules if r.food_id == "FOOD001308")
    product = {"@type": "Product", "name": stock.product, "sku": stock.sku, "offers": {
        "@type": "Offer", "price": "51", "priceCurrency": "TWD", "availability": "InStock",
    }}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text='<script type="application/ld+json">' + json.dumps(product) + "</script>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pages = recheck.PublicPages(client)
        row = recheck.recheck_confirmed(stock, pages)
        assert row["status"] == "可按毫升計價"
        assert row["picked"]["unit_price"] == "0.102"
        wrong_sku = recheck.recheck_confirmed(replace(stock, sku="other"), pages)
        assert wrong_sku["status"] == "查無可用報價"
        assert "price" not in wrong_sku["picked"]
        changed_pack = recheck.recheck_confirmed(replace(stock, checks=["1 L"]), pages)
        assert "包裝規格" in changed_pack["reason"]
        assert "price" not in changed_pack["picked"]
        product["offers"]["availability"] = "OutOfStock"
        out = recheck.recheck_confirmed(stock, recheck.PublicPages(client))
        assert out["status"] == "查無可用報價"
        assert "缺貨" in out["reason"] and "price" not in out["picked"]


def test_confirmed_estimate_is_not_a_retail_quote(monkeypatch):
    monkeypatch.setattr(recheck.time, "sleep", lambda _: None)
    _, rules = recheck.load_confirmed(recheck.CONFIRMED_RULES.read_bytes())
    pea, = [r for r in rules if r.kind == "market_estimate"]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=(
            "<h1>豌豆-甜豌豆</h1><p>本週平均批發價: 111 (元/公斤)</p>"
            "<p>預估零售價:<b>222</b> (元/公斤)</p>"
        ))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = recheck.recheck_confirmed(pea, recheck.PublicPages(client))
    assert row["status"] == "行情估價"
    assert row["picked"]["estimate"] is True
    assert row["picked"]["estimated_price"] == "222"
    assert row["picked"]["unit_price"] == "0.222"
    assert "price" not in row["picked"]
