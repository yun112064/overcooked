"""主食設定與解析（決策 #50、#52）：糙米飯改用 TFDA 生糙米、白米別名、不需要主食的寫法、新增主食選項，
以及給 API 的可選清單。營養以真實 T04 nutrition_model（TFDA）計算，不呼叫 LLM、不需資料庫。"""
import unittest
from decimal import Decimal

from app.services import meal_calculation as mc
from app.services import staples

D = Decimal


def energy(label):
    staple = staples.resolve(label)
    ln = staple.line
    [row] = mc.tfda_calculator([(ln, ln.grams_per_n)])
    return row


class BrownRiceTests(unittest.TestCase):
    def test_brown_rice_is_raw_japonica_brown_rice_eaten_as_is(self):
        staple = staples.resolve("糙米飯")
        ln = staple.line
        self.assertEqual((ln.grams_per_n, ln.adjustable_min, ln.adjustable_max), (D("75"), D("50"), D("125")))
        self.assertEqual((ln.weight_basis, ln.consumed_as_is), ("raw", True))
        self.assertIsNone(ln.unresolved_reason)
        row = energy("糙米飯")
        self.assertIsNone(row.error)
        self.assertEqual(row.trace["sample_id"], "A05013")
        self.assertEqual(row.nutrients["energy_kcal"], D("273"))   # 364 kcal/100 g × 75 g
        self.assertEqual(row.nutrients["protein_g"], D("5.55"))

    def test_snapshot_explains_raw_weight_and_cooked_equivalent(self):
        snap = staples.resolve("糙米").snapshot()
        self.assertEqual(snap["display_name"], "糙米（生）")
        self.assertEqual(snap["cooked_grams_per_n"], D("150"))
        self.assertEqual(snap["display_note"], "約煮成 150 g")


class AliasAndNoneTests(unittest.TestCase):
    def test_white_rice_raw_word_means_white_rice(self):
        self.assertEqual(staples.resolve("白米").label, "白飯")

    def test_ways_of_saying_no_staple(self):
        for text in ("不吃主食", "不要主食", "無主食", "不要", "免了", "不需", "不需要主食", "沒有主食", " 不用 "):
            with self.subTest(text=text):
                self.assertIsNone(staples.resolve(text))

    def test_unknown_text_is_still_unsupported_not_none(self):
        staple = staples.resolve("披薩")
        self.assertEqual(staple.line.unresolved_reason, "staple_not_supported")


class NewOptionTests(unittest.TestCase):
    def test_new_options_are_computable_and_close_to_150_g_white_rice(self):
        rice = energy("白飯").nutrients["energy_kcal"]  # 274.5 kcal
        for label in ("五穀飯", "胚芽米飯", "麵線", "烏龍麵", "玉米"):
            with self.subTest(label=label):
                row = energy(label)
                self.assertIsNone(row.error)
                self.assertIsNotNone(row.nutrients["protein_g"])
                self.assertLess(abs(row.nutrients["energy_kcal"] / rice - 1), D("0.1"))

    def test_options_for_the_api_list_label_aliases_and_display_text(self):
        options = {o["label"]: o for o in staples.staple_options()}
        self.assertTrue({"白飯", "糙米飯", "五穀飯", "胚芽米飯", "麵", "麵線", "烏龍麵", "麵包", "地瓜", "玉米"}
                        <= set(options))
        self.assertIn("白米", options["白飯"]["aliases"])
        self.assertEqual(options["糙米飯"]["display_note"], "約煮成 150 g")
        self.assertEqual(options["糙米飯"]["default_grams_per_n"], D("75"))
        self.assertEqual(options["白飯"]["display_name"], "白飯")
        self.assertTrue(all(o["review_status"] for o in options.values()))


if __name__ == "__main__":
    unittest.main()
