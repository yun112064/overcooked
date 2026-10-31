import unittest

from fastapi import HTTPException

from mock_store.app import create_app
from mock_store.db import Base, create_database
from mock_store.schemas import CheckoutInput, ImportShortagesInput
from mock_store.service import (
    advance_order,
    checkout,
    get_or_create_cart,
    import_shortages,
    products,
    seed_products,
)


class MockStoreFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine, SessionLocal = create_database("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = SessionLocal()
        seed_products(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_products_and_complete_delivery_flow(self):
        self.assertGreaterEqual(len(products(self.db)), 8)

        payload = ImportShortagesInput.model_validate(
            {
                "idempotency_key": "meal-result-001",
                "items": [
                    {"ingredient_id": "cabbage", "missing_grams": 700},
                    {"ingredient_id": "chicken_thigh", "missing_grams": 450},
                ],
            }
        )
        cart = import_shortages(self.db, payload)
        quantities = {item["product_id"]: item["quantity"] for item in cart["items"]}
        self.assertEqual(quantities, {"prd-cabbage": 2, "prd-chicken": 2})

        repeated = import_shortages(
            self.db,
            ImportShortagesInput.model_validate(
                {
                "cart_id": cart["id"],
                "idempotency_key": "meal-result-001",
                "items": [{"ingredient_id": "cabbage", "missing_grams": 700}],
                }
            ),
        )
        self.assertEqual(repeated["item_count"], 4)

        order = checkout(
            self.db,
            CheckoutInput(
                cart_id=cart["id"], recipient="林小姐", address="台北市示範路 13 號"
            ),
        )
        self.assertEqual(order["status"], "placed")
        self.assertEqual(order["total_twd"], order["subtotal_twd"] + 49)

        for expected in ("accepted", "picking", "delivering", "delivered"):
            order = advance_order(self.db, order["id"])
            self.assertEqual(order["status"], expected)
        self.assertEqual(len(order["events"]), 5)

    def test_checkout_rejects_empty_cart(self):
        cart = get_or_create_cart(self.db)
        self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            checkout(
                self.db,
                CheckoutInput(
                    cart_id=cart.id, recipient="林小姐", address="台北市示範路 13 號"
                ),
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_app_exposes_store_api_and_static_site(self):
        app = create_app("sqlite+pysqlite:///:memory:")
        paths = {route.path for route in app.routes}
        self.assertIn("/api/store/products", paths)
        self.assertIn("/api/store/carts/import-shortages", paths)
        self.assertIn("/api/store/orders", paths)
        self.assertIn("", paths)


if __name__ == "__main__":
    unittest.main()
