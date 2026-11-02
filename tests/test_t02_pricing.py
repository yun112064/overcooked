"""T02：全聯價格 Adapter 固定案例、查價排程的鎖與續跑保護（G02）。

全聯頁面皆為假 HTTP 客戶端回傳的合成資料，不對外連線。
"""
import json
import os
import unittest
from unittest.mock import patch
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, text

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all

TZ = ZoneInfo("Asia/Taipei")


def page(*products):
    docs = "".join(f'<script type="application/ld+json">{json.dumps(p, ensure_ascii=False)}</script>'
                   for p in products)
    return f"<html><body>{docs}</body></html>"


def product(name, price, *, availability="InStock", pid="1001", valid_until=None):
    offer = {"@type": "Offer", "price": price, "priceCurrency": "TWD",
             "availability": f"https://schema.org/{availability}"}
    if valid_until:
        offer["priceValidUntil"] = valid_until
    return {"@context": "https://schema.org", "@type": "Product", "name": name,
            "url": f"https://shop.pxgo.com.tw/product/{pid}", "offers": offer}


class FakeClient:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        request = httpx.Request("GET", url)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        if isinstance(self.behaviour, int):
            return httpx.Response(self.behaviour, request=request, text="blocked")
        return httpx.Response(200, request=request, text=self.behaviour)

    def close(self):
        pass


def adapter(behaviour, retries=1):
    from app.source_adapters.pxmart import PxMartAdapter
    client = FakeClient(behaviour)
    return PxMartAdapter(client=client, retries=retries, retry_delay=0, sleep=lambda s: None), client


class AdapterFixtureTests(unittest.TestCase):
    def quote(self, behaviour, retries=1):
        from app.source_adapters.price_models import IngredientQuery
        source, client = adapter(behaviour, retries)
        query = IngredientQuery(canonical_name="太白粉", food_ids=["FOOD000010"], aliases=["太白粉"])
        return source.quote(query), client

    def test_success_uses_decimal_price_and_package(self):
        quote, _ = self.quote(page(product("台糖高級太白粉 300g", "22")))
        self.assertEqual(quote.status.value, "success")
        self.assertEqual(quote.price, Decimal("22"))
        self.assertIsInstance(quote.price, Decimal)
        self.assertEqual((quote.package_quantity, quote.package_unit), (Decimal("300"), "g"))

    def test_failures_never_carry_a_price(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        cases = {
            "out_of_stock": page(product("台糖高級太白粉 300g", "22", availability="OutOfStock")),
            "missing_price": page(product("台糖高級太白粉 300g", None)),
            "unusable_package": page(product("台糖高級太白粉", "22")),
            "stale_price": page(product("台糖高級太白粉 300g", "22", valid_until=past)),
            "schema_changed": "<html><body><div>台糖高級太白粉 22元</div></body></html>",
            "not_found": page(product("太白粉炸雞塊 500g", "99")),
            "timeout": httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://x")),
            "source_error": 403,
        }
        for expected, behaviour in cases.items():
            with self.subTest(expected):
                quote, _ = self.quote(behaviour)
                self.assertEqual(quote.status.value, expected)
                self.assertIsNone(quote.price)  # 缺值不能填 0
                self.assertTrue(quote.missing_reason)

    def test_timeout_retries_are_bounded_and_blocking_is_not_retried(self):
        timeout = httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://x"))
        _, client = self.quote(timeout, retries=1)
        self.assertEqual(client.calls, 2)
        for status in (403, 429):
            _, client = self.quote(status, retries=3)
            self.assertEqual(client.calls, 1, status)

    def test_live_adapter_default_retry_delay_is_at_least_two_seconds(self):
        from app.source_adapters.pxmart import PxMartAdapter

        source = PxMartAdapter(client=FakeClient(page(product("台糖高級太白粉 300g", "22"))), retries=0)
        self.assertEqual(source.retry_delay, 2.0)

    def test_scheduler_uses_two_second_default_and_rejects_faster_rate(self):
        from app.pricing_scheduler import SchedulerSettings

        with patch.dict(os.environ, {"PRICE_COLLECTION_DELAY_SECONDS": "2.0"}):
            self.assertEqual(SchedulerSettings.from_env().delay_seconds, 2.0)
        with patch.dict(os.environ, {"PRICE_COLLECTION_DELAY_SECONDS": "1.5"}):
            with self.assertRaisesRegex(ValueError, "at least 2 seconds"):
                SchedulerSettings.from_env()


@requires_db
class CollectionLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0)
        self.addCleanup(self.engine.dispose)
        from app.source_adapters.price_models import IngredientQuery
        self.ingredients = [
            IngredientQuery(
                canonical_name="高麗菜",
                food_ids=["FOOD000005"],
                aliases=["甘藍"],
            )
        ]

    def advisory_locks(self):
        probe = create_engine(TEST_DATABASE_URL)
        try:
            with probe.connect() as conn:
                return conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")).scalar_one()
        finally:
            probe.dispose()

    def service(self, repository):
        from app.source_adapters.price_collection import PriceCollectionService
        return PriceCollectionService(
            repository=repository,
            ingredient_loader=lambda: list(self.ingredients),
            adapter_factory=lambda: adapter(page(product("高麗菜 1顆", "45")), retries=0)[0],
            delay_seconds=0, sleep=lambda s: None)

    def run_once(self, repository, key):
        return self.service(repository).run(trigger_type="manual", scheduled_for=datetime(2026, 10, 19, 3, tzinfo=TZ),
                                            run_key=key)

    def test_unlock_after_aborted_transaction_releases_lock(self):
        from app.source_adapters.pricing_repository import PricingRepository
        repository = PricingRepository(self.engine)
        lease = repository.try_begin_run(run_key="k1", provider="pxmart", trigger_type="manual",
                                         scheduled_for=datetime.now(TZ), started_at=datetime.now(TZ))
        with self.assertRaises(Exception):
            lease.connection.execute(text("SELECT 1 / 0"))  # 交易進入 aborted 狀態
        repository._unlock_and_close(lease.connection)
        self.assertEqual(self.advisory_locks(), 0)
        # 連線池只有一條連線：若帶著鎖回到池中，下一次查價會被誤判為已在執行
        self.assertEqual(self.run_once(repository, "k2").status, "completed_with_errors")

    def test_failing_finish_does_not_leave_lock_or_mask_error(self):
        from app.source_adapters.pricing_repository import PricingRepository

        class BrokenFinish(PricingRepository):
            calls = 0

            def finish_run(self, lease, **kwargs):
                BrokenFinish.calls += 1
                try:
                    lease.connection.execute(text("SELECT 1 / 0"))  # 模擬 UPDATE 失敗
                finally:
                    self._unlock_and_close(lease.connection)

        with self.assertRaises(Exception) as ctx:
            self.run_once(BrokenFinish(self.engine), "k1")
        self.assertIn("division by zero", str(ctx.exception))  # 原本的錯誤沒有被蓋掉
        self.assertEqual(BrokenFinish.calls, 1)
        self.assertEqual(self.advisory_locks(), 0)
        self.assertEqual(self.run_once(PricingRepository(self.engine), "k2").status, "completed_with_errors")

    def test_resume_progress_failure_finishes_run_and_unlocks(self):
        from app.source_adapters.pricing_repository import PricingRepository

        class BrokenProgress(PricingRepository):
            def get_run_progress(self, lease):
                raise RuntimeError("progress read failed")

        with self.assertRaises(RuntimeError):
            self.run_once(BrokenProgress(self.engine), "k1")
        self.assertEqual(self.advisory_locks(), 0)
        with self.engine.connect() as conn:
            status = conn.execute(text("SELECT status FROM price_collection_runs WHERE run_key = 'k1'")).scalar_one()
        self.assertEqual(status, "failed")
        # 失敗的 run 可用同一個 key 續跑
        self.assertEqual(self.run_once(PricingRepository(self.engine), "k1").status, "completed_with_errors")

    def test_exempt_ingredients_are_not_priced(self):
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.price_models import IngredientQuery
        ingredients = [
            *self.ingredients,
            IngredientQuery(canonical_name="水", food_ids=["FOOD000119"], aliases=[]),
        ]
        source, client = adapter(page(product("高麗菜 1顆", "45")), retries=0)
        service = PriceCollectionService(repository=PricingRepository(self.engine),
                                         ingredient_loader=lambda: ingredients,
                                         adapter_factory=lambda: source, delay_seconds=0, sleep=lambda s: None,
                                         exempt_food_ids={"FOOD000119"})
        result = service.run(trigger_type="manual", scheduled_for=datetime(2026, 10, 19, 3, tzinfo=TZ), run_key="w1")
        self.assertEqual(result.ingredient_total, 1)
        with self.engine.connect() as conn:
            names = conn.execute(text("SELECT canonical_name FROM price_quote_snapshots")).scalars().all()
            counts = conn.execute(text("SELECT status_counts FROM price_collection_runs WHERE run_key = 'w1'")).scalar_one()
        self.assertEqual(names, ["高麗菜"])
        self.assertEqual(counts["exempt"], 1)

    def test_default_exempt_list_contains_water(self):
        from app.source_adapters.price_collection import load_price_exempt
        self.assertIn("FOOD000119", load_price_exempt())

    def test_same_key_is_skipped_and_new_day_appends_snapshots(self):
        from app.source_adapters.pricing_repository import PricingRepository
        repository = PricingRepository(self.engine)
        self.assertNotEqual(self.run_once(repository, "pxmart-daily-2026-10-19").status, "skipped")
        self.assertEqual(self.run_once(repository, "pxmart-daily-2026-10-19").status, "skipped")
        self.run_once(repository, "pxmart-daily-2026-10-20")
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM price_quote_snapshots")).scalar_one(), 2)
        self.assertEqual(self.advisory_locks(), 0)


if __name__ == "__main__":
    unittest.main()
