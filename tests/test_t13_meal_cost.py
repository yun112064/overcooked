import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext

from app.meal_calc.types import IngredientRequirement
from app.pricing_sources.quotes import RetailPriceQuote
from app.pricing_sources.repository import RetailPricingRepository
from app.services.meal_cost import compute_meal_cost

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def requirement(ingredient_id: str, grams: str) -> IngredientRequirement:
    return IngredientRequirement(ingredient_id, Decimal(grams))


def quote(
    ingredient_id: str,
    price: str = "60",
    package_weight: str = "300",
    **changes,
) -> RetailPriceQuote:
    values = {
        "ingredient_id": ingredient_id,
        "product_id": 1,
        "source_product_id": "PX-1",
        "product_name": "測試高麗菜 300g",
        "retailer": "pxmart",
        "package_weight_g": Decimal(package_weight),
        "package_description": "300g/包",
        "package_price": Decimal(price),
        "currency": "TWD",
        "source_url": "https://example.test/products/PX-1",
        "source_method": "api",
        "observed_at": NOW - timedelta(hours=1),
        "valid_until": NOW + timedelta(days=1),
        "region": "台灣",
        "promotion_text": None,
    }
    values.update(changes)
    return RetailPriceQuote(**values)


class Ticket13CostTests(unittest.TestCase):
    def test_ac14_100g_from_300g_pack_costs_20_but_purchases_60(self):
        result = compute_meal_cost(
            [requirement("cabbage", "100")],
            [],
            Decimal("20"),
            lambda _: quote("cabbage"),
            now=NOW,
        )
        [line] = result.missing_items
        self.assertEqual((line.packages, line.purchase_price), (1, Decimal("60")))
        self.assertEqual(line.proportional_cost, Decimal("20"))
        self.assertEqual(result.budget_status, "OK")

    def test_packages_round_up_but_budget_uses_proportional_cost(self):
        result = compute_meal_cost(
            [requirement("cabbage", "450")], [], Decimal("90"),
            lambda _: quote("cabbage"), now=NOW,
        )
        [line] = result.missing_items
        self.assertEqual((line.packages, line.purchase_price), (2, Decimal("120")))
        self.assertEqual((result.cost_total, result.budget_status), (Decimal("90"), "OK"))

    def test_far_below_budget_is_still_valid(self):
        result = compute_meal_cost(
            [requirement("cabbage", "10")], [], Decimal("500"),
            lambda _: quote("cabbage"), now=NOW,
        )
        self.assertEqual((result.cost_total, result.budget_status), (Decimal("2"), "OK"))

    def test_owned_type_is_not_looked_up_but_keeps_recipe_grams(self):
        calls = []
        result = compute_meal_cost(
            [requirement("cabbage", "100")], ["cabbage"], Decimal("10"),
            lambda item: calls.append(item), now=NOW,
        )
        [line] = result.lines
        self.assertEqual(calls, [])
        self.assertEqual((line.required_grams, line.shortage_grams), (Decimal("100"), Decimal("0")))
        self.assertEqual((result.purchase_total, result.cost_total), (Decimal("0"), Decimal("0")))
        self.assertEqual(result.missing_items, ())

    def test_duplicate_recipe_lines_are_combined_before_buying_packages(self):
        calls = []
        result = compute_meal_cost(
            [requirement("cabbage", "200"), requirement("cabbage", "200")],
            [], Decimal("100"),
            lambda item: calls.append(item) or quote(item), now=NOW,
        )
        [line] = result.missing_items
        self.assertEqual(calls, ["cabbage"])
        self.assertEqual((line.required_grams, line.packages), (Decimal("400"), 2))

    def test_unknown_price_is_none_not_zero_and_keeps_known_subtotals(self):
        result = compute_meal_cost(
            [requirement("cabbage", "100"), requirement("carrot", "100")],
            [], Decimal("100"),
            lambda item: quote(item) if item == "cabbage" else None, now=NOW,
        )
        self.assertIsNone(result.purchase_total)
        self.assertIsNone(result.cost_total)
        self.assertEqual(result.known_purchase_total, Decimal("60"))
        self.assertEqual(result.known_cost_total, Decimal("20"))
        self.assertEqual(result.budget_status, "PRICE_INCOMPLETE")

    def test_known_cost_over_budget_wins_even_with_unknown_price(self):
        result = compute_meal_cost(
            [requirement("cabbage", "600"), requirement("carrot", "100")],
            [], Decimal("100"),
            lambda item: quote(item) if item == "cabbage" else None, now=NOW,
        )
        self.assertEqual(result.known_cost_total, Decimal("120"))
        self.assertEqual(result.budget_status, "BUDGET_EXCEEDED")

    def test_expired_price_is_unknown_and_keeps_traceability(self):
        expired = quote("cabbage", valid_until=NOW - timedelta(seconds=1))
        result = compute_meal_cost(
            [requirement("cabbage", "100")], [], Decimal("100"),
            lambda _: expired, now=NOW,
        )
        [line] = result.missing_items
        self.assertEqual(line.missing_reason, "PRICE_EXPIRED")
        self.assertEqual(line.quote.source_url, expired.source_url)
        self.assertIsNone(line.proportional_cost)

    def test_caller_decimal_precision_does_not_change_result(self):
        original = getcontext().prec
        try:
            getcontext().prec = 4
            result = compute_meal_cost(
                [requirement("cabbage", "100")], [], Decimal("100"),
                lambda _: quote("cabbage", price="50"), now=NOW,
            )
        finally:
            getcontext().prec = original
        self.assertGreater(result.cost_total, Decimal("16.6666"))
        self.assertLess(result.cost_total, Decimal("16.6667"))

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            compute_meal_cost([requirement("cabbage", "0")], [], Decimal("100"), lambda _: None, now=NOW)
        with self.assertRaises(ValueError):
            compute_meal_cost([], [], Decimal("-1"), lambda _: None, now=NOW)


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _FakeSession:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _FakeResult(self.row)


class Ticket02RepositoryBoundaryTests(unittest.TestCase):
    def test_verified_current_snapshot_is_mapped_with_traceability(self):
        row = {
            "product_id": 9,
            "source_product_id": "SKU-9",
            "product_name": "高麗菜 300g",
            "retailer": "pxmart",
            "package_weight_g": Decimal("300"),
            "package_description": "300g/包",
            "source_url": "https://example.test/SKU-9",
            "package_price": Decimal("60"),
            "currency": "TWD",
            "source_method": "api",
            "observed_at": NOW - timedelta(hours=1),
            "valid_until": NOW + timedelta(days=1),
            "region": "台灣",
            "promotion_text": "會員價",
        }
        db = _FakeSession(row)
        found = RetailPricingRepository(db).latest_valid_quote("cabbage", now=NOW)

        self.assertEqual((found.product_id, found.package_price), (9, Decimal("60")))
        self.assertEqual((found.observed_at, found.promotion_text), (row["observed_at"], "會員價"))
        sql, params = db.calls[0]
        self.assertIn("mapping.is_verified = TRUE", sql)
        self.assertIn("s.valid_until IS NULL OR s.valid_until >=", sql)
        self.assertEqual(params, {"ingredient_id": "cabbage", "now": NOW})

    def test_no_qualified_snapshot_returns_none(self):
        self.assertIsNone(
            RetailPricingRepository(_FakeSession(None)).latest_valid_quote(
                "missing", now=NOW
            )
        )


if __name__ == "__main__":
    unittest.main()
