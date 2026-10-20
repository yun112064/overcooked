"""T02 第二價格來源：萬家福線上購物（使用者決策 #25）。

萬家福頁面皆為假 HTTP 客戶端回傳的合成 HTML，不對外連線。
"""
import html
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, text

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all
from recipe_workbook import build, ingredient, recipe

TZ = ZoneInfo("Asia/Taipei")
SEARCH_MARKER = '<script>var pageDataLayer = {"page_type":"search-result-page"}</script>'


def tile(name, price, *, pid="2200300400101", brand=None, variant="1PC粒 x 1 x 1PC粒", available="true"):
    href = f"/zh/{brand}/{pid}.html" if brand else f"/zh/{pid}.html"
    return (f'<div class="hot-recommend-item line"><div class="box-img">'
            f'<a class="gtm-product-alink" href="{href}" data-pid="{pid}" data-price="{price}" '
            f'data-baseprice="{price}" data-brand="null" data-variant="{html.escape(variant)}" '
            f'data-name="{html.escape(name)}" data-ifavailable="{available}">'
            f'<img src="https://example.invalid/{pid}.jpg" alt="{html.escape(name)}"/></a></div></div>')


def search_page(*tiles):
    return f"<html><head>{SEARCH_MARKER}</head><body>{''.join(tiles)}</body></html>"


class FakeClient:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        request = httpx.Request("GET", url)
        behaviour = self.behaviour(url) if callable(self.behaviour) else self.behaviour
        if isinstance(behaviour, Exception):
            raise behaviour
        if isinstance(behaviour, int):
            return httpx.Response(behaviour, request=request, text="blocked")
        return httpx.Response(200, request=request, text=behaviour)

    def close(self):
        pass


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def uni_adapter(behaviour, retries=1, clock=None):
    from app.source_adapters.uni_prosperity import UniProsperityAdapter
    client = FakeClient(behaviour)
    clock = clock or FakeClock()
    return UniProsperityAdapter(client=client, retries=retries, retry_delay=0, sleep=clock.sleep,
                                clock=clock.monotonic), client


def cabbage():
    from app.source_adapters.price_models import IngredientQuery
    return IngredientQuery(canonical_name="高麗菜", food_ids=["FOOD000867"], aliases=["甘藍"])


class UniProsperityAdapterTests(unittest.TestCase):
    def test_parses_tile_facts_into_a_uni_prosperity_quote(self):
        source, client = uni_adapter(search_page(
            tile("UNIDESIGN冷凍高麗菜水餃", "59.0", pid="1525000100101", variant="910g克 x 1 x 1BOX盒"),
            tile("有機高麗菜800g", "99.0")))
        quote = source.quote(cabbage())
        self.assertEqual(quote.status.value, "success")
        self.assertEqual(quote.retailer, "萬家福")
        self.assertEqual(quote.product_name, "有機高麗菜800g")
        self.assertEqual((quote.price, quote.package_quantity, quote.package_unit),
                         (Decimal("99.0"), Decimal("800"), "g"))
        self.assertEqual(quote.product_id, "2200300400101")
        self.assertEqual(quote.product_url, "https://online.uni-prosperity.com.tw/zh/2200300400101.html")
        self.assertTrue(client.urls[0].startswith("https://online.uni-prosperity.com.tw/zh/search/?q="))
        # 只存事實資料：不保留圖片網址
        self.assertNotIn("example.invalid", quote.model_dump_json())

    def test_package_falls_back_to_variant_weight(self):
        source, _ = uni_adapter(search_page(tile("有機高麗菜", "99.0", variant="800g克 x 1 x 1Bag包")))
        quote = source.quote(cabbage())
        self.assertEqual((quote.status.value, quote.package_quantity, quote.package_unit),
                         ("success", Decimal("800"), "g"))

    def test_brand_comes_from_url_path(self):
        source, _ = uni_adapter(search_page(tile("奇美高麗菜 600g", "88", brand="%E5%A5%87%E7%BE%8E")))
        self.assertEqual(source.quote(cabbage()).brand, "奇美")

    def test_failures_never_carry_a_price(self):
        cases = {
            "not_found": search_page(tile("冰冰高麗菜豬肉熟水餃 799g", "118")),
            "out_of_stock": search_page(tile("有機高麗菜800g", "99.0", available="false")),
            "unusable_package": search_page(tile("有機高麗菜", "99.0")),
            "schema_changed": "<html><body>改版了</body></html>",
            "timeout": httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://x")),
            "source_error": 429,
        }
        for expected, behaviour in cases.items():
            with self.subTest(expected):
                quote = uni_adapter(behaviour)[0].quote(cabbage())
                self.assertEqual(quote.status.value, expected)
                self.assertIsNone(quote.price)
                self.assertTrue(quote.missing_reason)

    def test_normal_page_scripts_mentioning_captcha_are_not_treated_as_blocked(self):
        page = search_page(tile("有機高麗菜800g", "99.0")).replace(
            "</head>", "<script>var msg={verifyError:'驗證碼檢核錯誤'};</script>"
                       "<script src='https://www.google.com/recaptcha/api.js'></script></head>")
        self.assertEqual(uni_adapter(page)[0].quote(cabbage()).status.value, "success")

    def test_empty_search_page_is_not_found(self):
        quote = uni_adapter(search_page())[0].quote(cabbage())
        self.assertEqual(quote.status.value, "not_found")

    def test_requests_are_spaced_at_least_two_seconds(self):
        clock = FakeClock()
        source, client = uni_adapter(search_page(), clock=clock)
        source.quote(cabbage())  # 高麗菜、甘藍 → 兩次搜尋
        source.quote(cabbage())
        self.assertGreaterEqual(len(client.urls), 4)
        self.assertGreaterEqual(clock.now - 1000.0, 2.0 * (len(client.urls) - 1))

    def test_identifiable_user_agent(self):
        from app.source_adapters.uni_prosperity import UniProsperityAdapter
        source = UniProsperityAdapter()
        try:
            self.assertIn("overcooked", source.client.headers["User-Agent"])
        finally:
            source.close()


class RobotsPolicyTests(unittest.TestCase):
    def test_disallowed_paths_are_refused(self):
        from app.source_adapters.uni_prosperity import is_allowed_url
        base = "https://online.uni-prosperity.com.tw"
        for path in ("/on/demandware.store/Sites-Uniprosperity-Site/default/Search-UpdateGrid?q=x",
                     "/zh/search/?q=x&cgid=fresh", "/zh/search/?q=x&srule=price-low",
                     "/zh/search/?q=x&prefn1=brand&prefv1=a", "/zh/search/?q=x&pmin=1&pmax=9",
                     "/zh/filter/brand/", "/zh/cart/", "/zh/checkout/", "/zh/account/", "/zh/login/",
                     "/zh/order-history/"):
            with self.subTest(path):
                self.assertFalse(is_allowed_url(base + path))
        self.assertFalse(is_allowed_url("https://evil.example/zh/search/?q=x"))
        self.assertTrue(is_allowed_url(base + "/zh/search/?q=%E9%AB%98"))
        self.assertTrue(is_allowed_url(base + "/zh/2200300400101.html"))

    def test_adapter_never_requests_a_disallowed_url(self):
        from app.source_adapters import uni_prosperity
        source, client = uni_adapter(search_page())
        original = uni_prosperity.SEARCH_URL
        uni_prosperity.SEARCH_URL = "https://online.uni-prosperity.com.tw/zh/search/?q={query}&srule=x"
        try:
            quote = source.quote(cabbage())
        finally:
            uni_prosperity.SEARCH_URL = original
        self.assertEqual(client.urls, [])
        self.assertEqual(quote.status.value, "source_error")
        self.assertIn("robots", quote.missing_reason)


def px_page(*names_prices):
    import json
    docs = "".join(
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@type": "Product", "name": name,
            "url": f"https://shop.pxgo.com.tw/product/{i}",
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "TWD",
                       "availability": "https://schema.org/InStock"}}, ensure_ascii=False) + "</script>"
        for i, (name, price) in enumerate(names_prices))
    return f"<html><body>{docs}找不到商品</body></html>"


@requires_db
class FallbackCollectionTests(unittest.TestCase):
    """全聯查無／不可用的食材才查萬家福；統計分來源；可續跑、冪等。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0)
        self.addCleanup(self.engine.dispose)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workbook = build(Path(folder.name) / "book.xlsx", [recipe("R001", "高麗菜炒蛋", ingredients=[
            ingredient("FOOD000867", "高麗菜", 300), ingredient("FOOD001566", "雞蛋", 100),
            ingredient("FOOD000678", "胡蘿蔔", 50)])])
        self.uni_clients = []

    def px(self, url):
        if "%E9%9B%9E%E8%9B%8B" in url:  # 雞蛋：全聯有貨
            return px_page(("大成雞蛋 10入 600g", "75"))
        if "%E8%83%A1%E8%98%BF%E8%94%94" in url:  # 胡蘿蔔：全聯只有無重量包裝
            return px_page(("胡蘿蔔", "30"))
        return px_page()  # 高麗菜：全聯查無

    def uni(self, url):
        if "%E9%AB%98%E9%BA%97%E8%8F%9C" in url:
            return search_page(tile("有機高麗菜800g", "99.0"))
        return search_page()

    def service(self):
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.pxmart import PxMartAdapter

        def uni_factory():
            source, client = uni_adapter(self.uni, retries=0)
            self.uni_clients.append(client)
            return source

        return PriceCollectionService(
            workbook_path=self.workbook, repository=PricingRepository(self.engine),
            adapter_factory=lambda: PxMartAdapter(client=FakeClient(self.px), retries=0, retry_delay=0,
                                                  sleep=lambda s: None),
            fallback_adapter_factory=uni_factory, delay_seconds=0, sleep=lambda s: None,
            exempt_food_ids=set(), synonym_groups=[])

    def run_key(self, service, key="pxmart-daily-2026-10-20", **kwargs):
        return service.run(trigger_type="manual", scheduled_for=datetime(2026, 10, 20, 3, tzinfo=TZ),
                           run_key=key, **kwargs)

    def test_only_px_misses_are_queried_at_uni_prosperity_and_stats_are_per_source(self):
        result = self.run_key(self.service())
        self.assertEqual((result.provider, result.auto_adopted_count, result.missing_count), ("pxmart", 1, 2))
        self.assertEqual(result.fallback.provider, "uniprosperity")
        self.assertEqual((result.fallback.ingredient_total, result.fallback.auto_adopted_count,
                          result.fallback.missing_count), (2, 1, 1))
        searched = " ".join(url for client in self.uni_clients for url in client.urls)
        self.assertNotIn("%E9%9B%9E%E8%9B%8B", searched)  # 全聯已成功的雞蛋不查萬家福
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT s.provider, s.canonical_name, s.status, r.run_key FROM price_quote_snapshots s "
                "JOIN price_collection_runs r ON r.id = s.run_id ORDER BY s.provider, s.canonical_name")).all()
            runs = dict(conn.execute(text("SELECT provider, status FROM price_collection_runs")).all())
        self.assertIn(("uniprosperity", "高麗菜", "success", "pxmart-daily-2026-10-20:uniprosperity"), rows)
        self.assertEqual(sum(1 for r in rows if r[0] == "uniprosperity"), 2)
        self.assertEqual(set(runs), {"pxmart", "uniprosperity"})

    def test_px_estimates_are_also_queried_at_uni_prosperity(self):
        """全聯只有估價（review_required）不算已知價格，也送萬家福查實際價格；工作簿對應失敗仍不送。"""
        from app.source_adapters.pricing_repository import PricingRepository, RunLease
        snapshots = [("查無", "not_found", {"selection_tier": "missing"}),
                     ("估價", "review_required", {"selection_tier": "estimate"}),
                     ("已成功", "success", {"selection_tier": "exact"}),
                     ("對應失敗", "not_found", {"selection_tier": "missing",
                                               "selection_method": "workbook_mapping"})]
        with self.engine.connect() as conn:
            run_id = conn.execute(text(
                "INSERT INTO price_collection_runs (id, run_key, provider, trigger_type, scheduled_for, "
                "started_at, status, ingredient_total) VALUES (gen_random_uuid(), 'px', 'pxmart', 'manual', "
                "now(), now(), 'completed', 4) RETURNING id")).scalar_one()
            for name, status, payload in snapshots:
                conn.execute(text(
                    "INSERT INTO price_quote_snapshots (run_id, provider, canonical_name, status, queried_at, "
                    "currency, source_url, quote_payload) VALUES (:run_id, 'pxmart', :name, :status, now(), "
                    "'TWD', 'https://example.test/', CAST(:payload AS jsonb))"),
                    {"run_id": run_id, "name": name, "status": status, "payload": json.dumps(payload)})
            conn.commit()
            lease = RunLease(run_id="unused", run_key="px:uniprosperity", connection=conn, provider="uniprosperity")
            targets, finished = PricingRepository(self.engine).fallback_targets(lease, primary_run_key="px")
        self.assertEqual((targets, finished), ({"查無", "估價"}, True))

    def test_rerun_is_idempotent_and_backfills_an_already_completed_px_run(self):
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.pxmart import PxMartAdapter
        # 先前只跑過全聯（沒有第二來源）
        PriceCollectionService(
            workbook_path=self.workbook, repository=PricingRepository(self.engine),
            adapter_factory=lambda: PxMartAdapter(client=FakeClient(self.px), retries=0, retry_delay=0,
                                                  sleep=lambda s: None),
            delay_seconds=0, sleep=lambda s: None, exempt_food_ids=set(), synonym_groups=[],
        ).run(trigger_type="manual", scheduled_for=datetime(2026, 10, 20, 3, tzinfo=TZ),
              run_key="pxmart-daily-2026-10-20")
        backfill = self.run_key(self.service())
        self.assertEqual(backfill.status, "skipped")  # 全聯那次已完成，不重查
        self.assertEqual(backfill.fallback.auto_adopted_count, 1)
        again = self.run_key(self.service())
        self.assertEqual((again.status, again.fallback.status), ("skipped", "skipped"))
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM price_quote_snapshots")).scalar_one(), 5)

    def test_partial_fallback_resumes_with_the_same_key(self):
        first = self.run_key(self.service(), key="k", batch_size=1)
        self.assertEqual(first.fallback.status, "partial")
        self.run_key(self.service(), key="k", batch_size=1)
        third = self.run_key(self.service(), key="k", batch_size=1)
        self.assertEqual(third.fallback.checkpoint_count, 2)
        with self.engine.connect() as conn:
            uni = conn.execute(text(
                "SELECT count(*) FROM price_quote_snapshots WHERE provider = 'uniprosperity'")).scalar_one()
        self.assertEqual(uni, 2)


@requires_db
class CrossSourceQuoteTests(unittest.TestCase):
    """T13 缺料價格：兩個來源都有可用價時，取完整包裝且單位價最低者並記錄理由。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL)
        self.addCleanup(self.engine.dispose)
        self.keys = iter(range(1000))

    def seed(self, provider, *quotes):
        from app.source_adapters.pricing_repository import PricingRepository
        now = datetime.now(timezone.utc)
        repo = PricingRepository(self.engine)
        lease = repo.try_begin_run(run_key=f"{provider}-{next(self.keys)}", provider=provider,
                                   trigger_type="manual", scheduled_for=now, started_at=now)
        for item in quotes:
            repo.save_quote(lease, item)
        repo.finish_run(lease, finished_at=now, status="completed", success_count=0, review_count=0,
                        failure_count=0, duration_ms=1, status_counts={}, missing_reasons={})

    def latest(self, food_id="FOOD000867"):
        from sqlalchemy.orm import Session
        from app.services.meal_cost import latest_quote
        with Session(self.engine) as db:
            return latest_quote(db, food_id)

    def uni_quote(self, price, qty, **extra):
        from test_t13_meal_cost import quote
        return quote("FOOD000867", price, qty, retailer="萬家福", source="萬家福線上購物 public search",
                     product_url="https://online.uni-prosperity.com.tw/zh/2200300400101.html", **extra)

    def test_uni_prosperity_price_is_used_when_px_has_none(self):
        from test_t13_meal_cost import missing_quote
        self.seed("pxmart", missing_quote("FOOD000867"))
        self.seed("uniprosperity", self.uni_quote("99", "800"))
        chosen = self.latest()
        self.assertEqual((chosen.retailer, chosen.price), ("萬家福", Decimal("99")))

    def test_lowest_unit_price_wins_and_reason_is_recorded(self):
        from test_t13_meal_cost import quote
        self.seed("pxmart", quote("FOOD000867", "45", "300"))  # 15 元/100 g
        self.seed("uniprosperity", self.uni_quote("99", "800"))  # 12.375 元/100 g
        chosen = self.latest()
        self.assertEqual(chosen.retailer, "萬家福")
        self.assertTrue(any("lowest unit price" in reason for reason in chosen.selection_reasons))
        self.seed("uniprosperity", self.uni_quote("200", "800"))  # 萬家福新快照變貴 → 改回全聯
        self.assertEqual(self.latest().retailer, "PX Mart")

    def test_failures_stay_unknown(self):
        from test_t13_meal_cost import missing_quote
        self.seed("pxmart", missing_quote("FOOD000867"))
        self.seed("uniprosperity", missing_quote("FOOD000867", retailer="萬家福"))
        chosen = self.latest()
        self.assertIsNone(chosen.price)
        self.assertEqual(chosen.retailer, "萬家福")


class SchedulerWiringTests(unittest.TestCase):
    def test_fallback_source_is_on_by_default_and_can_be_disabled(self):
        import os
        from unittest import mock
        from app.pricing_scheduler import SchedulerSettings, create_service
        from app.source_adapters.uni_prosperity import UniProsperityAdapter
        with mock.patch.dict(os.environ, {"PRICE_FALLBACK_UNI_PROSPERITY_ENABLED": "true"}):
            factory = create_service(SchedulerSettings.from_env()).fallback_adapter_factory
        with factory() as source:
            self.assertIsInstance(source, UniProsperityAdapter)
        with mock.patch.dict(os.environ, {"PRICE_FALLBACK_UNI_PROSPERITY_ENABLED": "false"}):
            self.assertIsNone(create_service(SchedulerSettings.from_env()).fallback_adapter_factory)


class ResultSourceNameTests(unittest.TestCase):
    def test_price_items_carry_the_chinese_source_name(self):
        from app.services.meal_results import _prices
        line = {"food_id": "FOOD000867", "shortage_grams": "50", "price_status": "known", "packs": 1,
                "purchase_price": "99", "cost": "6.1875", "missing_reason": None,
                "quote": {"retailer": "萬家福", "queried_at": "2026-10-20T03:00:00+00:00"}}
        px = {**line, "food_id": "FOOD001566", "quote": {"retailer": "PX Mart"}}
        payload = {"cost": {"status": "computed", "lines": [line, px]}, "calculation": {"ingredients": []}}
        items = _prices(payload)["items"]
        self.assertEqual([i["source_name"] for i in items], ["萬家福", "全聯"])
        self.assertEqual(items[0]["queried_at"], "2026-10-20T03:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
