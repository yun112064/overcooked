import sys
import unittest
from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from scripts.run_pxmart_catalog_batch import initial_state, run_batch, atomic_json


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.rows = [{'ingredient_id': 'ING0052', 'canonical_name': '雞胸肉', 'search_keyword': '雞胸肉'},
                     {'ingredient_id': 'ING0009', 'canonical_name': '雞蛋', 'search_keyword': '雞蛋'}]

    def test_runs_and_resume_without_double_fetch(self):
        seen, sleeps = [], []
        def fetch(keyword, terms, page_size):
            seen.append(keyword)
            return 1, [SimpleNamespace(product_name='生鮮雞胸肉', package_weight_g=Decimal('300'),
                     package_price=Decimal('139'), manual_review_reason=None,
                     source_record_id='p1', source_url='https://example.test/p1',
                     promotion_text=None, region=None, source_method='pxmart_search')]
        state = initial_state()
        list(run_batch(self.rows, state, page_size=5, interval_seconds=3, fetcher=fetch, sleeper=sleeps.append))
        self.assertEqual(len(seen), 2)
        self.assertEqual(sleeps, [3])
        self.assertEqual(state['runs']['ING0052']['pending_review'], 1)
        list(run_batch(self.rows, state, page_size=5, interval_seconds=3, fetcher=fetch, sleeper=sleeps.append))
        self.assertEqual(len(seen), 2)

    def test_failure_continues_and_records(self):
        def fetch(*args, **kwargs):
            raise TimeoutError('offline-test')
        state = initial_state()
        list(run_batch(self.rows, state, page_size=5, interval_seconds=3, fetcher=fetch, sleeper=lambda _: None))
        self.assertEqual(state['runs']['ING0009']['status'], 'failed')

    def test_atomic_progress_written(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'progress.json'
            atomic_json(target, initial_state())
            self.assertTrue(target.exists())
            self.assertFalse(target.with_name('progress.json.tmp').exists())


if __name__ == '__main__':
    unittest.main()
