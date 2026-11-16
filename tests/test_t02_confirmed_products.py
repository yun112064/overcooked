"""T02 使用者確認商品頁：每日查價第三段與已確認同義食材計價。全部為合成頁面，不對外連線。"""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all
from test_t02_uni_prosperity import FakeClient, px_page, search_page, tile, uni_adapter

TZ = ZoneInfo("Asia/Taipei")
CARROT_URL = "https://shop.pxgo.com.tw/hourArrive/goods/1-2-3"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda _: None)


def carrot_rule(**updates):
    from app.source_adapters.confirmed_products import ConfirmedRule
    rule = ConfirmedRule(food_id="FOOD000678", name="胡蘿蔔", url=CARROT_URL, product="胡蘿蔔 500g", kind="retail",
                         quantity=500, unit="g", basis="商品頁標 500 g。", sku="1-2-3", package_g=500)
    return replace(rule, **updates)


def product_page(name="胡蘿蔔 500g", sku="1-2-3", price="35", availability="InStock"):
    product = {"@type": "Product", "name": name, "sku": sku,
               "offers": {"@type": "Offer", "price": price, "priceCurrency": "TWD", "availability": availability}}
    return '<script type="application/ld+json">' + json.dumps(product, ensure_ascii=False) + "</script>"


def adapter(rules, page):
    from app.source_adapters.confirmed_products import ConfirmedProductAdapter

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=page() if callable(page) else page)

    return ConfirmedProductAdapter({r.food_id: r for r in rules},
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))


def carrot():
    from app.source_adapters.price_models import IngredientQuery
    return IngredientQuery(canonical_name="胡蘿蔔", food_ids=["FOOD000678"])


def test_confirmed_page_becomes_a_usable_quote_with_the_approved_package_basis():
    from app.source_adapters.price_models import QuoteStatus, SelectionTier
    from app.source_adapters.price_scope import quote_unusable_reason
    quote = adapter([carrot_rule()], product_page()).quote(carrot())
    assert (quote.status, quote.selection_tier, quote.price) == (QuoteStatus.SUCCESS, SelectionTier.AUTO_ADOPTED,
                                                                 Decimal("35"))
    assert (quote.package_quantity, quote.package_unit, quote.retailer) == (Decimal("500"), "g", "PX Mart")
    assert quote.selection_method == "confirmed_product_rule" and "500 g" in quote.selection_reasons[0]
    assert quote_unusable_reason(quote, datetime.now(timezone.utc)) is None


@pytest.mark.parametrize("page, status, fragment", [
    (product_page(availability="OutOfStock"), "out_of_stock", "缺貨"),
    (product_page(sku="9-9-9"), "not_found", "SKU"),
    (product_page(name="胡蘿蔔 1kg"), "not_found", "品名"),
    ("<html>改版了</html>", "not_found", "唯一商品"),
])
def test_failed_checks_never_carry_a_price(page, status, fragment):
    quote = adapter([carrot_rule()], page).quote(carrot())
    assert quote.status.value == status and quote.price is None
    assert fragment in quote.missing_reason


def test_blocked_source_is_a_source_error_without_price():
    from app.source_adapters.confirmed_products import ConfirmedProductAdapter

    def handler(request):
        return httpx.Response(403, text="blocked")

    source = ConfirmedProductAdapter({"FOOD000678": carrot_rule()},
                                     client=httpx.Client(transport=httpx.MockTransport(handler)))
    quote = source.quote(carrot())
    assert quote.status.value == "source_error" and quote.price is None


def test_food_without_a_rule_is_not_found():
    quote = adapter([], product_page()).quote(carrot())
    assert quote.status.value == "not_found" and "no single confirmed product rule" in quote.missing_reason


def test_market_estimate_stays_an_estimate():
    from app.source_adapters.confirmed_products import load_confirmed_rules
    pea = next(r for r in load_confirmed_rules().values() if r.kind == "market_estimate")
    page = "<h1>豌豆-甜豌豆</h1><p>預估零售價:<b>222</b> (元/公斤)</p>"
    from app.source_adapters.price_models import IngredientQuery
    quote = adapter([pea], page).quote(IngredientQuery(canonical_name=pea.name, food_ids=[pea.food_id]))
    assert (quote.status.value, quote.selection_tier.value) == ("review_required", "estimate")
    assert quote.price is None and quote.estimated_price == Decimal("222")


def test_all_79_versioned_rules_load():
    from app.source_adapters.confirmed_products import load_confirmed_rules
    assert len(load_confirmed_rules()) == 79


def test_scheduler_enables_the_confirmed_stage_by_default(monkeypatch):
    from app.pricing_scheduler import SchedulerSettings
    monkeypatch.delenv("PRICE_CONFIRMED_PRODUCTS_ENABLED", raising=False)
    assert SchedulerSettings.from_env().confirmed_products is True
    monkeypatch.setenv("PRICE_CONFIRMED_PRODUCTS_ENABLED", "false")
    assert SchedulerSettings.from_env().confirmed_products is False


@requires_db
class ConfirmedStageTests(unittest.TestCase):
    """只查全聯與萬家福都沒有可用報價、又有確認商品頁的食材；分開記錄；可重跑。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0)
        self.addCleanup(self.engine.dispose)
        self.confirmed_urls = []

    def px(self, url):
        if "%E9%9B%9E%E8%9B%8B" in url:  # 雞蛋：全聯有貨
            return px_page(("大成雞蛋 10入 600g", "75"))
        return px_page()

    def uni(self, url):
        if "%E9%AB%98%E9%BA%97%E8%8F%9C" in url:  # 高麗菜：萬家福有貨
            return search_page(tile("有機高麗菜800g", "99.0"))
        return search_page()

    def service(self, page=None):
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.price_models import IngredientQuery
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.pxmart import PxMartAdapter

        def page_for():
            self.confirmed_urls.append(CARROT_URL)
            return page or product_page()

        egg_rule = carrot_rule(food_id="FOOD001566", name="雞蛋")  # 全聯已成功，不應再查
        return PriceCollectionService(
            repository=PricingRepository(self.engine),
            ingredient_loader=lambda: [
                IngredientQuery(canonical_name="高麗菜", food_ids=["FOOD000867"]),
                IngredientQuery(canonical_name="雞蛋", food_ids=["FOOD001566"]),
                IngredientQuery(canonical_name="胡蘿蔔", food_ids=["FOOD000678"]),
                IngredientQuery(canonical_name="洋蔥", food_ids=["FOOD000618"]),
            ],
            adapter_factory=lambda: PxMartAdapter(client=FakeClient(self.px), retries=0, retry_delay=0,
                                                  sleep=lambda s: None),
            fallback_adapter_factory=lambda: uni_adapter(self.uni, retries=0)[0],
            confirmed_adapter_factory=lambda: adapter([carrot_rule(), egg_rule], page_for),
            confirmed_food_ids={"FOOD000678", "FOOD001566"},
            delay_seconds=0, sleep=lambda s: None, exempt_food_ids=set(), synonym_groups=[])

    def run_key(self, service, key="pxmart-daily-2026-11-16"):
        return service.run(trigger_type="manual", scheduled_for=datetime(2026, 11, 16, 3, tzinfo=TZ), run_key=key)

    def test_only_foods_both_stores_missed_and_with_a_rule_are_fetched(self):
        from app.source_adapters.price_scope import latest_quotes
        from sqlalchemy.orm import Session

        result = self.run_key(self.service())
        self.assertEqual((result.confirmed.provider, result.confirmed.ingredient_total,
                          result.confirmed.auto_adopted_count), ("confirmed", 1, 1))
        self.assertEqual(len(self.confirmed_urls), 1)  # 只有胡蘿蔔；洋蔥沒有規則、雞蛋已有價
        with Session(self.engine) as db:
            quote = latest_quotes(db, ["FOOD000678"], now=datetime.now(timezone.utc))["FOOD000678"]
        self.assertEqual((quote.price, quote.selection_method), (Decimal("35"), "confirmed_product_rule"))
        again = self.run_key(self.service())
        self.assertEqual(again.confirmed.status, "skipped")

    def test_failed_confirmed_page_stays_unknown(self):
        from app.source_adapters.price_scope import latest_quotes
        from sqlalchemy.orm import Session

        result = self.run_key(self.service(page=product_page(availability="OutOfStock")))
        self.assertEqual((result.confirmed.auto_adopted_count, result.confirmed.missing_count), (0, 1))
        with Session(self.engine) as db:
            quote = latest_quotes(db, ["FOOD000678"], now=datetime.now(timezone.utc))["FOOD000678"]
        self.assertIsNone(quote.price)

    def test_confirmed_synonym_is_priced_by_its_partner(self):
        from app.meal_calc.types import IngredientRequirement
        from app.services.meal_cost import meal_cost_from_db
        from sqlalchemy.orm import Session

        self.run_key(self.service())
        with Session(self.engine) as db:
            # 椰菜 FOOD001130 與高麗菜 FOOD000867 是已確認同義（SYN006）；椰菜本身沒有報價
            cost = meal_cost_from_db(db, [IngredientRequirement("FOOD001130", Decimal("400"))], (), None,
                                     now=datetime.now(timezone.utc))
        line, = cost.lines
        self.assertEqual((line.price_status, line.packs, line.purchase_price), ("known", 1, Decimal("99.0")))
        self.assertIn("confirmed synonym: priced by FOOD000867", line.quote.selection_reasons[-1])
