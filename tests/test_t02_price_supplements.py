"""T13 價格補充（2026-11-16 使用者決定）：換算、已含在其他食材、估價與整包計價。合成報價，不對外連線。"""
import copy
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all
from test_t02_uni_prosperity import FakeClient, px_page, search_page, uni_adapter

TZ = ZoneInfo("Asia/Taipei")
EGG, BOILED, YOLK, SHIITAKE_WATER, DRIED_SHIITAKE = "FOOD001566", "FOOD000147", "FOOD000945", "FOOD000701", "FOOD000877"
ANGELICA, RED_WINE, UNPRICED = "FOOD001145", "FOOD000650", "FOOD000999"


def config():
    from app.services.price_supplements import SUPPLEMENTS_PATH
    return json.loads(SUPPLEMENTS_PATH.read_text(encoding="utf-8"))


def test_versioned_config_loads_with_cited_rules():
    from app.services.price_supplements import load_supplements
    rules = load_supplements()
    assert {BOILED, YOLK, SHIITAKE_WATER} <= set(rules.derived)
    assert rules.derived[YOLK].ratio == Decimal("2.94") and rules.derived[YOLK].source_food_id == EGG
    assert rules.estimates[RED_WINE].whole_package and rules.estimates[ANGELICA].observed == "2026-11-16"
    assert not set(rules.derived) & set(rules.estimates)  # 每種食材只有一條規則
    assert all(r.source_url.startswith("https://") and r.basis for r in rules.estimates.values())
    assert not {r.source_food_id for r in rules.derived.values()} & set(rules.derived)  # 不連鎖換算


@pytest.mark.parametrize("section, index, field, value", [
    ("derived", 0, "ratio", "0"),
    ("derived", 0, "source_food_id", "FOOD000147"),   # 自己換算自己
    ("derived", 0, "basis", ""),
    ("estimates", 0, "package_unit", "ml"),
    ("estimates", 0, "source_url", "http://example.test"),
    ("estimates", 0, "price", "-1"),
    ("estimates", 0, "observed", "yesterday"),
])
def test_malformed_rules_are_rejected(section, index, field, value):
    from app.services.price_supplements import parse_supplements
    data = copy.deepcopy(config())
    data[section][index][field] = value
    with pytest.raises(ValueError):
        parse_supplements(data)


@requires_db
class SupplementCostTests(unittest.TestCase):
    """沒有自己報價的食材：換算 → 已含 → 估價 → 整包；仍查不到者維持未知。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.price_models import IngredientQuery
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.pxmart import PxMartAdapter
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0)
        self.addCleanup(self.engine.dispose)
        PriceCollectionService(
            repository=PricingRepository(self.engine),
            ingredient_loader=lambda: [IngredientQuery(canonical_name="雞蛋", food_ids=[EGG])],
            adapter_factory=lambda: PxMartAdapter(client=FakeClient(lambda url: px_page(("大成雞蛋 10入 600g", "75"))),
                                                  retries=0, retry_delay=0, sleep=lambda s: None),
            fallback_adapter_factory=lambda: uni_adapter(lambda url: search_page(), retries=0)[0],
            delay_seconds=0, sleep=lambda s: None, exempt_food_ids=set(), synonym_groups=[],
        ).run(trigger_type="manual", scheduled_for=datetime(2026, 11, 16, 3, tzinfo=TZ), run_key="eggs")

    def cost(self, *requirements, budget=None):
        from app.meal_calc.types import IngredientRequirement
        from app.services.meal_cost import meal_cost_from_db
        from sqlalchemy.orm import Session
        with Session(self.engine) as db:
            return meal_cost_from_db(db, [IngredientRequirement(f, Decimal(g)) for f, g in requirements], (), budget,
                                     now=datetime.now(timezone.utc))

    def line(self, cost, food_id):
        return next(line for line in cost.lines if line.food_id == food_id)

    def test_cooked_egg_uses_the_egg_quote_at_equal_mass(self):
        line = self.line(self.cost((BOILED, "120")), BOILED)
        self.assertEqual((line.price_status, line.price_basis, line.derived_from_food_id), ("known", "derived", EGG))
        self.assertEqual(line.exact_cost, Fraction(120, 600) * 75)
        self.assertEqual(line.shortage_grams, Decimal("120"))  # 顯示的是水煮蛋自己的用量

    def test_yolk_ratio_is_an_estimate(self):
        line = self.line(self.cost((YOLK, "18")), YOLK)
        self.assertEqual((line.price_status, line.price_basis), ("estimated", "derived"))
        self.assertEqual(line.exact_cost, Fraction(Decimal("18") * Decimal("2.94")) / 600 * 75)

    def test_soaking_water_is_included_when_the_menu_buys_dried_shiitake(self):
        cost = self.cost((SHIITAKE_WATER, "100"), (DRIED_SHIITAKE, "15"))
        line = self.line(cost, SHIITAKE_WATER)
        self.assertEqual((line.price_status, line.cost, line.derived_from_food_id), ("included", 0, DRIED_SHIITAKE))
        self.assertNotIn(line, cost.missing_items)

    def test_soaking_water_without_a_priced_source_stays_unknown(self):
        line = self.line(self.cost((SHIITAKE_WATER, "100")), SHIITAKE_WATER)  # 乾香菇本身沒有報價
        self.assertEqual(line.price_status, "unknown")

    def test_cited_estimate_counts_in_cost_and_budget(self):
        cost = self.cost((ANGELICA, "2"), (EGG, "60"), budget=Decimal("1000"))
        line = self.line(cost, ANGELICA)
        self.assertEqual((line.price_status, line.packs, line.purchase_price), ("estimated", 1, Decimal("190")))
        self.assertEqual(line.cost, Decimal("3.8"))
        self.assertIn("2026-11-16", line.estimate_note)
        self.assertTrue(cost.price_complete)
        self.assertEqual((cost.budget_status, cost.estimated_food_ids), ("OK", (ANGELICA,)))
        self.assertEqual(cost.cost_total, Decimal("3.8") + Decimal("7.5"))

    def test_whole_package_charges_one_package(self):
        line = self.line(self.cost((RED_WINE, "375")), RED_WINE)
        self.assertEqual((line.price_status, line.packs, line.purchase_price, line.cost),
                         ("estimated", 1, Decimal("268"), Decimal("268")))

    def test_food_without_any_rule_stays_unknown(self):
        cost = self.cost((UNPRICED, "50"), (ANGELICA, "2"))
        self.assertEqual(self.line(cost, UNPRICED).price_status, "unknown")
        self.assertFalse(cost.price_complete)
        self.assertIsNone(cost.cost_total)

    def test_expired_store_price_becomes_a_dated_historical_estimate(self):
        from datetime import timedelta
        from app.meal_calc.types import IngredientRequirement
        from app.services.meal_cost import _price_by_supplements
        from app.source_adapters.price_scope import latest_quotes
        from sqlalchemy.orm import Session
        now = datetime.now(timezone.utc)
        item = IngredientRequirement(EGG, Decimal("60"))
        with Session(self.engine) as db:
            quote = latest_quotes(db, [EGG], now=now)[EGG].model_copy(update={"valid_until": now - timedelta(hours=1)})
            line = _price_by_supplements(db, [item], {item: quote}, now, None, menu_food_ids={EGG})[item]
        self.assertEqual((line.price_status, line.price_basis, line.cost), ("estimated", "historical", Decimal("7.5")))
        self.assertIn("歷史價格", line.estimate_note)

    def test_derived_food_shares_the_source_package(self):
        cost = self.cost((EGG, "100"), (BOILED, "100"))
        egg, boiled = self.line(cost, EGG), self.line(cost, BOILED)
        self.assertEqual((egg.packs + boiled.packs, cost.known_purchase_total), (1, Decimal("75")))
        self.assertEqual(cost.known_cost_total, Decimal("25"))

    def test_split_whole_package_demand_is_charged_once(self):
        from fractions import Fraction as F
        from app.meal_calc.types import IngredientRequirement
        from app.services.meal_cost import meal_cost_from_db
        from sqlalchemy.orm import Session
        with Session(self.engine) as db:
            cost = meal_cost_from_db(db, [IngredientRequirement(RED_WINE, Decimal("100")),
                                          IngredientRequirement(RED_WINE, Decimal("200"), F(200))], (), None,
                                     now=datetime.now(timezone.utc))
        self.assertEqual((cost.known_cost_total, cost.known_purchase_total), (Decimal("268"), Decimal("268")))

    def test_market_estimate_from_the_confirmed_stage_is_used(self):
        import httpx
        from app.services.price_supplements import load_supplements  # noqa: F401
        from app.source_adapters.confirmed_products import ConfirmedProductAdapter, load_confirmed_rules
        from app.source_adapters.price_collection import PriceCollectionService
        from app.source_adapters.price_models import IngredientQuery
        from app.source_adapters.pricing_repository import PricingRepository
        from app.source_adapters.pxmart import PxMartAdapter
        pea = next(r for r in load_confirmed_rules().values() if r.kind == "market_estimate")

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            return httpx.Response(200, text="<h1>豌豆-甜豌豆</h1><p>預估零售價:<b>222</b> (元/公斤)</p>")

        PriceCollectionService(
            repository=PricingRepository(self.engine),
            ingredient_loader=lambda: [IngredientQuery(canonical_name=pea.name, food_ids=[pea.food_id])],
            adapter_factory=lambda: PxMartAdapter(client=FakeClient(lambda url: px_page()), retries=0, retry_delay=0,
                                                  sleep=lambda s: None),
            fallback_adapter_factory=lambda: uni_adapter(lambda url: search_page(), retries=0)[0],
            confirmed_adapter_factory=lambda: ConfirmedProductAdapter(
                {pea.food_id: pea}, client=httpx.Client(transport=httpx.MockTransport(handler))),
            confirmed_food_ids={pea.food_id}, delay_seconds=0, sleep=lambda s: None, exempt_food_ids=set(),
            synonym_groups=[],
        ).run(trigger_type="manual", scheduled_for=datetime(2026, 11, 16, 3, tzinfo=TZ), run_key="pea")
        line = self.line(self.cost((pea.food_id, "200")), pea.food_id)
        self.assertEqual((line.price_status, line.price_basis, line.cost), ("estimated", "market_estimate", Decimal("44.4")))

    def test_payload_marks_estimates_for_the_frontend(self):
        from app.services.generation import _jsonable, cost_snapshot
        from app.services.meal_results import _prices
        snapshot = json.loads(json.dumps(_jsonable(cost_snapshot(self.cost((ANGELICA, "2"), (EGG, "60"))))))
        self.assertTrue(snapshot["price_estimated"])
        prices = _prices({"cost": snapshot, "calculation": {"ingredients": [
            {"food_id": ANGELICA, "name": "當歸"}, {"food_id": EGG, "name": "雞蛋"}]}})
        item = next(i for i in prices["items"] if i["food_id"] == ANGELICA)
        self.assertEqual((item["price_status"], item["price_basis"]), ("estimated", "estimate"))
        self.assertTrue(item["estimate_url"].startswith("https://"))
        self.assertTrue(prices["price_estimated"])


def test_historical_fallback_requires_a_twd_quote_that_only_expired():
    from datetime import timedelta
    from app.services.meal_cost import _historical_reason
    from app.source_adapters.price_models import (Availability, ConfidenceLevel, PriceQuote, QuoteStatus,
                                                  SelectionTier)
    now = datetime.now(timezone.utc)
    quote = PriceQuote(canonical_name="雞蛋", food_ids=[EGG], source_url="https://example.test/", queried_at=now - timedelta(days=5),
                       valid_until=now - timedelta(days=3), status=QuoteStatus.SUCCESS,
                       selection_tier=SelectionTier.AUTO_ADOPTED, confidence=ConfidenceLevel.HIGH, price=Decimal("75"),
                       currency="TWD", package_quantity=Decimal("600"), package_unit="g", availability=Availability.IN_STOCK)
    assert _historical_reason(quote, now)
    assert not _historical_reason(quote.model_copy(update={"currency": "USD"}), now)
    assert not _historical_reason(quote.model_copy(update={"valid_until": None}), now)  # 沒過期就不是歷史價


def test_price_retry_also_looks_up_derived_sources_and_synonyms():
    from app.services.result_retry import lookup_food_ids
    assert lookup_food_ids([BOILED, "FOOD001130"]) == [BOILED, EGG, "FOOD001130", "FOOD000867"]


def test_blocked_confirmed_host_pauses_the_run():
    import httpx
    from app.source_adapters.confirmed_products import ConfirmedProductAdapter
    from test_t02_confirmed_products import carrot, carrot_rule
    source = ConfirmedProductAdapter({"FOOD000678": carrot_rule()},
                                     client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))))
    quote = source.quote(carrot())
    assert quote.missing_reason.startswith("source blocked or rate-limited") and quote.price is None
