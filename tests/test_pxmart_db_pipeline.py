import unittest
from scripts.run_pxmart_db_pipeline import prepare


class PipelineTests(unittest.TestCase):
    def test_filter_and_deduplicate(self):
        p = {'retailer': 'pxmart', 'source_product_id': '7', 'product_name': '原味生鮮雞胸肉',
             'package_weight_g': '300', 'package_price': '100', 'source_url': 'https://example.org/7',
             'review_status': 'pending_manual_match', 'review_reason': None}
        state = {'version': 1, 'source': 'pxbox_online', 'updated_at_utc': '2026-10-17T00:00:00+00:00',
                 'runs': {'a': {'status': 'ok'}, 'b': {'status': 'ok'}, 'c': {'status': 'no_parsed_products'}},
                 'products_by_ingredient': {'a': [p, dict(p, review_status='rejected')], 'b': [p]}}
        rows, stats = prepare(state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats['no_parsed'], 1)
        self.assertEqual(rows[0]['source_product_id'], '7')

    def test_conflict_rejected(self):
        p = {'retailer': 'pxmart', 'source_product_id': '7', 'product_name': '生鮮雞胸肉',
             'package_weight_g': '300', 'package_price': '100',
             'review_status': 'pending_manual_match', 'review_reason': None}
        s = {'version': 1, 'source': 'pxbox_online', 'updated_at_utc': '2026-10-17T00:00:00+00:00',
             'runs': {'a': {'status': 'ok'}, 'b': {'status': 'ok'}},
             'products_by_ingredient': {'a': [p], 'b': [dict(p, package_price='200')]}}
        with self.assertRaises(ValueError):
            prepare(s)

    def test_wrong_state_rejected(self):
        with self.assertRaises(ValueError):
            prepare({})


if __name__ == '__main__':
    unittest.main()
