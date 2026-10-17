"""Offline tests: no network, database writes, or government-market imports."""
import unittest
from decimal import Decimal

from backend.app.pricing_sources.common import parse_weight_g, product_matches
from backend.app.pricing_sources.PXMart_api import parse_product
from backend.app.pricing_sources.Carrefour_crawler import parse_product_page


class RetailSourceParserTests(unittest.TestCase):
    def test_weight(self):
        self.assertEqual(parse_weight_g("白米 500g"), Decimal("500"))
        self.assertIsNone(parse_weight_g("牛奶 1000ml"))
        self.assertIsNone(parse_weight_g("青蔥 約200g"))

    def test_total_package_weight(self):
        self.assertEqual(parse_weight_g("100g/包 共10包"), Decimal("1000"))
        self.assertEqual(parse_weight_g("100g x 10包"), Decimal("1000"))
        self.assertEqual(parse_weight_g("100g×10入"), Decimal("1000"))
        self.assertEqual(parse_weight_g("300g/包"), Decimal("300"))
        self.assertIsNone(parse_weight_g("100g/包 共10包 另贈50g"))
        self.assertIsNone(parse_weight_g("100g/包 共10包 共12包"))

    def test_pxmart_bundle_pricing(self):
        base = {
            "id": "testbundle", "product_name": "雞胸肉(100g/包 共10包)",
            "sale_price": 565, "price_currency": "TWD",
            "product_categories": [{"d1_category_name": "食品/料理", "d2_category_name": "冷凍", "d3_category_name": "肉類"}],
        }
        record = parse_product(base, ["雞胸肉", "雞胸"])
        self.assertIsNotNone(record)
        self.assertEqual(record.package_weight_g, Decimal("1000"))
        self.assertEqual(record.unit_price_per_g * 100, Decimal("56.5"))
        base["product_tag_labels"] = [{"spec_name": "規格/容量", "tags": [{"tag_name": "100g/包"}]}]
        self.assertIsNone(parse_product(base, ["雞胸肉", "雞胸"]))

    def test_processed_product_rejected(self):
        self.assertFalse(product_matches("蒜蓉醬 200g", ["大蒜", "蒜頭"]))
        self.assertTrue(product_matches("台灣蒜頭 300g", ["大蒜", "蒜頭"]))

    def test_pxmart_product(self):
        item = {
            "id": "test001",
            "product_name": "麵粉 500g",
            "sale_price": 45,
            "price_currency": "TWD",
            "product_categories": [{"d1_category_name":"食品/料理", "d2_category_name":"烘焙", "d3_category_name":"粉類"}],
        }
        record = parse_product(item, ["麵粉"])
        self.assertIsNotNone(record)
        self.assertEqual(record.package_weight_g, Decimal("500"))
        self.assertEqual(record.unit_price_per_g * Decimal("100"), Decimal("9"))

    def test_carrefour_jsonld(self):
        html = '''<script type="application/ld+json">{"@type":"Product", "name":"測試麵粉 500g", "sku":"test001", "offers":{"url":"https://example.test/item", "availability":"https://schema.org/InStock", "priceCurrency":"TWD", "price":"45"}}</script>'''
        record = parse_product_page(html, ["測試麵粉"])
        self.assertIsNotNone(record)
        self.assertEqual(record.package_weight_g, Decimal("500"))
        self.assertEqual(record.package_price, Decimal("45"))


if __name__ == "__main__":
    unittest.main()
