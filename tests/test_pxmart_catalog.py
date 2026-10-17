import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pxmart_catalog import build_catalog

class CatalogTests(unittest.TestCase):
    def test_aliases_and_unique_ids(self):
        result = build_catalog([('ING2','雞胸肉'),('ING2','雞胸肉'),('ING1','番茄')], [('ING1','蕃茄'),('ING2','雞胸')])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['aliases'], ['蕃茄'])
        self.assertFalse(result[1]['enabled'])
    def test_conflicting_canonical_names_are_rejected(self):
        with self.assertRaises(ValueError):
            build_catalog([('ING2','雞胸肉'),('ING2','牛肉')], [])

if __name__ == '__main__':
    unittest.main()
