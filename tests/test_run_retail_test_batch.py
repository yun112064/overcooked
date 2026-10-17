import ast
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_retail_test_batch.py'
NODES = ast.parse(SCRIPT.read_text(encoding='utf-8'))
F = {n.name: n for n in NODES.body if isinstance(n, ast.FunctionDef)}

class SafetyTests(unittest.TestCase):
    def test_database_guard_exists(self):
        self.assertIn('checked_test_url', F)
        self.assertIn('meal_planner_retail_test', SCRIPT.read_text(encoding='utf-8'))
    def test_runlog_records_start_and_finish(self):
        self.assertIn('record_start', F)
        self.assertIn('record_finish', F)
    def test_no_live_crawling(self):
        s = SCRIPT.read_text(encoding='utf-8')
        self.assertNotIn('fetch_products(', s)
        self.assertNotIn('fetch_hour_arrive_product(', s)

if __name__ == '__main__':
    unittest.main()
