"""T13：缺少種類、完整採購價與比例成本（R08；AC14、AC15）。

價格皆為合成的 PriceQuote，不對外連線。
"""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.meal_calc.types import IngredientRequirement
from app.services.meal_cost import compute_meal_cost, latest_quote, meal_cost_from_db
from app.source_adapters.price_models import PriceQuote
from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all

NOW = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
CABBAGE = "FOOD000867"
CARROT = "FOOD000678"
EGG = "FOOD001566"
TOMATO = "FOOD001016"
TOMATO_SYN = "FOOD001444"  # 與番茄為已確認同義組 SYN005
WATER = "FOOD000119"


def quote(food_id, price, qty="300", unit="g", **extra):
    fields = dict(
        canonical_name=f"食材{food_id}", food_ids=[food_id],
        source_url="https://shop.pxgo.com.tw/search?q=x", queried_at=NOW - timedelta(hours=3),
        region_condition="未選地區", store_condition="未選門市",
        status="success", selection_tier="auto_adopted", confidence="high",
        product_id="P1", product_name=f"商品{food_id} {qty}{unit}", price=Decimal(price), currency="TWD",
        package_quantity=Decimal(qty), package_unit=unit, package_description=f"{qty}{unit}/包",
        product_url="https://shop.pxgo.com.tw/product/P1", availability="in_stock", price_kind="displayed",
    )
    fields.update(extra)
    return PriceQuote(**fields)


def missing_quote(food_id, reason="not_found", **extra):
    return PriceQuote(canonical_name=f"食材{food_id}", food_ids=[food_id],
                      source_url="https://shop.pxgo.com.tw/search?q=x", queried_at=NOW,
                      status=reason, missing_reason=f"{reason} 說明", **extra)


def lookup(quotes):
    calls = []

    def quote_for(food_id):
        calls.append(food_id)
        return quotes.get(food_id)

    return quote_for, calls


def req(food_id, grams):
    return IngredientRequirement(food_id=food_id, grams=Decimal(grams))


class ProportionalCostTests(unittest.TestCase):
    def test_100g_needed_from_300g_pack_at_60_buys_60_and_costs_20(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "100")], set(), Decimal("100"), quote_for, now=NOW)

        [line] = result.missing_items
        self.assertEqual(line.shortage_grams, Decimal("100"))
        self.assertEqual(line.packs, 1)
        self.assertEqual(line.purchase_price, Decimal("60"))
        self.assertEqual(line.cost, Decimal("20"))
        self.assertEqual(result.purchase_total, Decimal("60"))
        self.assertEqual(result.cost_total, Decimal("20"))
        self.assertEqual(result.budget_status, "OK")


class BudgetTests(unittest.TestCase):
    def test_packs_round_up_but_cost_stays_proportional(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "450")], set(), Decimal("90"), quote_for, now=NOW)

        [line] = result.missing_items
        self.assertEqual((line.packs, line.purchase_price, line.cost), (2, Decimal("120"), Decimal("90")))
        self.assertEqual(result.budget_status, "OK")  # 成本 90 = 預算 90，採購 120 不參與判定

    def test_far_below_budget_still_passes(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "10")], set(), Decimal("500"), quote_for, now=NOW)

        self.assertEqual(result.cost_total, Decimal("2"))
        self.assertEqual(result.budget_status, "OK")

    def test_cost_above_budget_is_exceeded(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "100.5")], set(), Decimal("20"), quote_for, now=NOW)

        self.assertEqual(result.cost_total, Decimal("20.1"))
        self.assertEqual(result.budget_status, "BUDGET_EXCEEDED")

    def test_unrounded_decimal_cost(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "50", "300")})
        result = compute_meal_cost([req(CABBAGE, "100")], set(), Decimal("20"), quote_for, now=NOW)

        self.assertGreater(result.cost_total, Decimal("16.6666"))
        self.assertLess(result.cost_total, Decimal("16.6667"))


class UnknownPriceTests(unittest.TestCase):
    def assert_unknown(self, food_quote, reason):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300"), CARROT: food_quote})
        result = compute_meal_cost([req(CABBAGE, "100"), req(CARROT, "200")], set(), Decimal("100"),
                                   quote_for, now=NOW)

        line = next(line for line in result.missing_items if line.food_id == CARROT)
        self.assertIsNone(line.cost)
        self.assertIsNone(line.purchase_price)
        self.assertEqual(line.price_status, "unknown")
        self.assertEqual(line.missing_reason, reason)
        self.assertIsNone(result.cost_total)
        self.assertIsNone(result.purchase_total)
        self.assertEqual(result.known_cost_total, Decimal("20"))
        self.assertEqual(result.known_purchase_total, Decimal("60"))
        self.assertEqual(result.budget_status, "PRICE_INCOMPLETE")
        return line

    def test_no_snapshot_is_unknown(self):
        self.assert_unknown(None, "NO_PRICE_DATA")

    def test_failed_quote_keeps_source_reason(self):
        line = self.assert_unknown(missing_quote(CARROT, "out_of_stock"), "out_of_stock 說明")
        self.assertEqual(line.quote.status, "out_of_stock")

    def test_estimate_is_not_a_known_price(self):
        estimate = missing_quote(CARROT, "review_required", selection_tier="estimate",
                                 estimated_price=Decimal("25"), estimated_product_name="估價商品")
        self.assert_unknown(estimate, "review_required 說明")

    def test_volume_package_cannot_convert_from_grams(self):
        self.assert_unknown(quote(CARROT, "40", "500", unit="ml"), "UNIT_MISMATCH")

    def test_expired_quote_is_unknown(self):
        self.assert_unknown(quote(CARROT, "40", "500", valid_until=NOW - timedelta(days=1)), "PRICE_EXPIRED")

    def test_known_costs_over_budget_win_over_unknown(self):
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "600"), req(CARROT, "100")], set(), Decimal("100"),
                                   quote_for, now=NOW)

        self.assertEqual(result.known_cost_total, Decimal("120"))
        self.assertEqual(result.budget_status, "BUDGET_EXCEEDED")

    def test_exempt_water_is_known_zero_without_lookup(self):
        quote_for, calls = lookup({})
        result = compute_meal_cost([req(WATER, "500")], set(), Decimal("10"), quote_for, now=NOW)

        self.assertEqual(calls, [])
        self.assertEqual(result.cost_total, Decimal("0"))
        self.assertEqual(result.purchase_total, Decimal("0"))
        self.assertEqual(result.budget_status, "OK")
        self.assertEqual(result.missing_items, [])


SALT = "FOOD000691"


class SeasoningTests(unittest.TestCase):
    """決策 #44：調味料視為家中足量——缺量 0、不查價、不列缺料、成本 0；營養由呼叫端照配方計算。"""

    def test_seasoning_is_treated_like_owned_without_lookup(self):
        quote_for, calls = lookup({CABBAGE: quote(CABBAGE, "60", "300")})
        result = compute_meal_cost([req(CABBAGE, "100"), req(SALT, "3")], set(), Decimal("100"), quote_for,
                                   now=NOW, seasoning_food_ids={SALT})

        self.assertEqual(calls, [CABBAGE])
        salt = next(line for line in result.lines if line.food_id == SALT)
        self.assertEqual(salt.price_status, "seasoning")
        self.assertEqual(salt.required_grams, Decimal("3"))  # 配方用量保留供營養計算
        self.assertEqual((salt.shortage_grams, salt.purchase_price, salt.cost), (Decimal(0), Decimal(0), Decimal(0)))
        self.assertEqual([line.food_id for line in result.missing_items], [CABBAGE])
        self.assertTrue(result.price_complete)
        self.assertEqual(result.purchase_total, Decimal("60"))

    def test_default_seasonings_come_from_the_config_file(self):
        from app.services.seasonings import load_seasonings
        seasonings = load_seasonings()
        # 鹽、醬油、雞粉、米酒、油、太白粉（勾芡）、白胡椒粉
        for food_id in ("FOOD000691", "FOOD001541", "FOOD001562", "FOOD000352", "FOOD000472", "FOOD000090",
                        "FOOD000289"):
            self.assertIn(food_id, seasonings)
        # 主料用的麵粉、生鮮辛香（大蒜、青蔥）、水（另由免計價設定處理）不是調味料
        for food_id in ("FOOD000077", "FOOD000377", "FOOD000039", "FOOD000598", WATER, CABBAGE):
            self.assertNotIn(food_id, seasonings)

    def test_config_list_follows_its_rules_or_states_a_reason(self):
        import json

        from app.services.seasonings import SEASONINGS_PATH, classify
        data = json.loads(SEASONINGS_PATH.read_text(encoding="utf-8"))
        for food_id, entry in data["ingredients"].items():
            with self.subTest(food_id=food_id, name=entry["name"]):
                self.assertTrue(classify(food_id, entry["name"]) or entry.get("reason"))
        self.assertFalse(set(data["exclude"]) & set(data["ingredients"]))
        self.assertIsNone(classify("FOOD000650", "紅酒"))       # exclude 優先於規則
        self.assertEqual(classify(None, "鮮味炒手"), "調味粉")
        self.assertIsNone(classify(None, "中筋麵粉"))

    def test_compute_uses_config_seasonings_by_default(self):
        quote_for, calls = lookup({})
        result = compute_meal_cost([req(SALT, "3")], set(), Decimal("10"), quote_for, now=NOW)
        self.assertEqual(calls, [])
        self.assertEqual(result.missing_items, [])
        self.assertEqual(result.cost_total, Decimal("0"))


class TraceabilityTests(unittest.TestCase):
    def test_missing_line_keeps_package_source_time_and_conditions(self):
        valid_until = NOW + timedelta(days=2)
        quote_for, _ = lookup({CABBAGE: quote(CABBAGE, "60", "300", valid_until=valid_until)})
        [line] = compute_meal_cost([req(CABBAGE, "100")], set(), Decimal("100"), quote_for, now=NOW).missing_items

        self.assertEqual(line.price_status, "known")
        self.assertIsNone(line.missing_reason)
        self.assertEqual(line.quote.package_quantity, Decimal("300"))
        self.assertEqual(line.quote.package_unit, "g")
        self.assertEqual(line.quote.package_description, "300g/包")
        self.assertEqual(line.quote.product_url, "https://shop.pxgo.com.tw/product/P1")
        self.assertEqual(line.quote.queried_at, NOW - timedelta(hours=3))
        self.assertEqual(line.quote.valid_until, valid_until)
        self.assertEqual(line.quote.region_condition, "未選地區")


class OwnedIngredientTests(unittest.TestCase):
    def test_owned_ingredient_has_zero_shortage_no_lookup_and_is_not_listed(self):
        quote_for, calls = lookup({CABBAGE: quote(CABBAGE, "60", "300"), CARROT: quote(CARROT, "30", "500")})
        result = compute_meal_cost([req(CABBAGE, "100"), req(CARROT, "250")], {CABBAGE}, Decimal("100"),
                                   quote_for, now=NOW)

        self.assertEqual(calls, [CARROT])
        owned = next(line for line in result.lines if line.food_id == CABBAGE)
        self.assertTrue(owned.owned)
        self.assertEqual(owned.required_grams, Decimal("100"))  # 營養仍按配方 100 g
        self.assertEqual(owned.shortage_grams, Decimal("0"))
        self.assertEqual(owned.purchase_price, Decimal("0"))
        self.assertEqual(owned.cost, Decimal("0"))
        self.assertEqual([line.food_id for line in result.missing_items], [CARROT])
        self.assertEqual(result.purchase_total, Decimal("30"))
        self.assertEqual(result.cost_total, Decimal("15"))

    def test_all_owned_gives_both_totals_zero(self):
        quote_for, calls = lookup({})
        result = compute_meal_cost([req(CABBAGE, "100"), req(EGG, "120")], {CABBAGE, EGG}, Decimal("50"),
                                   quote_for, now=NOW)

        self.assertEqual(calls, [])
        self.assertEqual(result.missing_items, [])
        self.assertEqual(result.purchase_total, Decimal("0"))
        self.assertEqual(result.cost_total, Decimal("0"))
        self.assertEqual(result.budget_status, "OK")

    def test_confirmed_synonym_counts_as_owned(self):
        quote_for, calls = lookup({})
        result = compute_meal_cost([req(TOMATO_SYN, "150")], {TOMATO}, Decimal("50"), quote_for, now=NOW)

        self.assertEqual(calls, [])
        self.assertEqual(result.cost_total, Decimal("0"))


@requires_db
class LatestQuoteTests(unittest.TestCase):
    """唯讀查 T02 的 price_quote_snapshots；快照經 T02 PricingRepository 寫入。"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.engine = create_engine(TEST_DATABASE_URL)
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine)

    def save_run(self, key, *quotes):
        from app.source_adapters.pricing_repository import PricingRepository
        repo = PricingRepository(self.engine)
        lease = repo.try_begin_run(run_key=key, provider="pxmart", trigger_type="manual",
                                   scheduled_for=NOW, started_at=NOW)
        for q in quotes:
            repo.save_quote(lease, q)
        repo.finish_run(lease, finished_at=NOW, status="completed", success_count=0, review_count=0,
                        failure_count=0, duration_ms=1, status_counts={}, missing_reasons={})

    def test_end_to_end_100g_from_300g_pack(self):
        self.save_run("r1", quote(CABBAGE, "60", "300"))
        with self.Session() as db:
            result = meal_cost_from_db(db, [req(CABBAGE, "100")], set(), Decimal("100"), now=NOW)

        [line] = result.missing_items
        self.assertEqual((line.purchase_price, line.cost), (Decimal("60"), Decimal("20")))
        self.assertEqual(line.quote.product_url, "https://shop.pxgo.com.tw/product/P1")
        self.assertEqual(line.quote.queried_at, NOW - timedelta(hours=3))

    def test_newer_success_wins(self):
        self.save_run("r1", quote(CABBAGE, "60", "300", queried_at=NOW - timedelta(days=2)))
        self.save_run("r2", quote(CABBAGE, "75", "300", queried_at=NOW - timedelta(days=1)))
        with self.Session() as db:
            self.assertEqual(latest_quote(db, CABBAGE).price, Decimal("75"))

    def test_failed_newer_run_keeps_last_success_with_its_original_time(self):
        older = NOW - timedelta(days=2)
        self.save_run("r1", quote(CABBAGE, "60", "300", queried_at=older))
        self.save_run("r2", missing_quote(CABBAGE, "timeout"))
        with self.Session() as db:
            found = latest_quote(db, CABBAGE)
        self.assertEqual(found.price, Decimal("60"))
        self.assertEqual(found.queried_at, older)

    def test_never_succeeded_returns_failure_and_unknown_food_returns_none(self):
        self.save_run("r1", missing_quote(CARROT, "not_found"))
        with self.Session() as db:
            self.assertEqual(latest_quote(db, CARROT).status, "not_found")
            self.assertIsNone(latest_quote(db, EGG))

    def test_merged_synonym_quote_found_by_any_food_id(self):
        merged = quote(TOMATO, "45", "500", canonical_name="番茄", food_ids=[TOMATO, TOMATO_SYN])
        self.save_run("r1", merged)
        with self.Session() as db:
            self.assertEqual(latest_quote(db, TOMATO_SYN).price, Decimal("45"))


if __name__ == "__main__":
    unittest.main()
