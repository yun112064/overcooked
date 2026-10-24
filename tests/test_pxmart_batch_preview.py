import importlib.util
import sys
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'batch_pxmart_preview.py'
# local test should be run from project root where backend exists
spec = importlib.util.spec_from_file_location('batch_pxmart_preview', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@dataclass
class FakeRecord:
    source_record_id: str = '123'
    product_name: str = '生鮮雞胸肉(300g)'
    package_weight_g: Decimal = Decimal('300')
    package_price: Decimal = Decimal('130')
    source_url: str = 'https://example.invalid'
    promotion_text: str | None = None
    region: str | None = 'online'
    source_method: str | None = 'test'
    manual_review_reason: str | None = None

class BatchPreviewTest(unittest.TestCase):
    def test_unseasoned_chicken_pending_review(self):
        r = mod.preview_records({'ingredient_id':'ING0052','canonical_name':'雞胸肉'}, [FakeRecord()])
        self.assertEqual(r[0]['review_status'], 'pending_manual_match')
        self.assertEqual(r[0]['price_type'], 'online_listed_unverified_regular')

    def test_seasoned_chicken_rejected(self):
        r = mod.preview_records({'ingredient_id':'ING0052','canonical_name':'雞胸肉'}, [FakeRecord(product_name='紐奧良雞胸肉(300g)')])
        self.assertEqual(r[0]['review_status'], 'rejected')

    def test_processed_egg_rejected(self):
        r = mod.preview_records({'ingredient_id':'ING0009','canonical_name':'雞蛋'}, [FakeRecord(product_name='雞蛋布丁(300g)')])
        self.assertEqual(r[0]['review_status'], 'rejected')

    def test_unverified_weight_rejected(self):
        r = mod.preview_records({'ingredient_id':'ING0057','canonical_name':'白米'}, [FakeRecord(product_name='白米',package_weight_g=Decimal('0'))])
        self.assertEqual(r[0]['review_status'], 'rejected')

if __name__ == '__main__':
    unittest.main()
