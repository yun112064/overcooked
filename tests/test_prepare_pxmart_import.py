import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_pxmart_import import transform


class TransformTests(unittest.TestCase):
    def test_only_pending_and_dedup(self):
        base = dict(retailer='pxmart', price_channel='pxbox_online',
                    price_type='online_listed_unverified_regular',
                    source_product_id='431578', product_name='生鮮雞胸肉',
                    package_weight_g='300', package_price='139',
                    review_status='pending_manual_match', source_method='pxmart_search')
        payload = {'generated_at_utc': '2026-10-17T06:49:10+00:00',
                   'products': [base, dict(base), dict(base, review_status='rejected'),
                                dict(base, source_product_id='2', price_channel='unknown')]}
        got = transform(payload)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['source_product_id'], '431578')
        self.assertNotIn('ingredient_id_candidate', got[0])

    def test_reject_naive_timestamp(self):
        with self.assertRaises(ValueError):
            transform({'generated_at_utc': '2026-10-17T06:49:10', 'products': []})

    def test_reject_wrong_shape(self):
        with self.assertRaises(ValueError):
            transform({'generated_at_utc': '2026-10-17T06:49:10+00:00'})

    def test_reject_bad_ttl(self):
        with self.assertRaises(ValueError):
            transform({'generated_at_utc': '2026-10-17T06:49:10+00:00', 'products': []}, ttl_hours=0)


if __name__ == '__main__':
    unittest.main()
