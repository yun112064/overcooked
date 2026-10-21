"""decisions #45：TFDA 樣本營養欄位空白時，先套「食品類別上必然為 0」規則，其餘交給 nutrient_fill（7 種營養素）。

測試的介面（seams）：
1. nutrient_category_zero.zero_rule：設定檔（config/nutrient_category_zero.json）的類別碼＋營養素規則；
2. make_tfda_calculator(...)：類別 0（trace 標示 category_zero）與 7 種營養素的缺值推估，
   涵蓋無烹調方式、生樣本＋烹調係數、熟樣本三種路徑；TFDA 有值一律優先；不適用者維持 None；
3. calculate_meal：與使用者菜單類似的整餐（假推估值）7 種營養素都有值；
4. estimation.store.check_fill：7 種營養素的合理範圍與一致性，類別 0 者拒絕；
5. estimation.batch：掃描 7 種營養素（排除類別 0）、含主食樣本，dry-run 回報預估費用。
絕不呼叫真實 OpenAI：推估值一律為測試建立的快取物件或假 responder。
"""
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.estimation import batch, store
from app.meal_calc.types import EstimatedFactor
from app.services import meal_calculation as mc
from app.services import nutrient_category_zero as cz
from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from recipe_workbook import build, ingredient, recipe

D = Decimal
MODEL = "gpt-5.4-mini-2026-03-17"
ALL = ("energy_kcal", "carbohydrate_g", "protein_g", "fat_g", "sugars_g", "sodium_mg", "dietary_fiber_g")


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name, grams_per_n=D(grams), **kw)


def fill(sample_id, nutrient, value):
    return EstimatedFactor(
        estimate_id=f"fill-{sample_id}-{nutrient}", kind="nutrient_fill", food_id=sample_id, key=nutrient,
        source="llm_estimated", status="llm_estimated", model=MODEL, prompt_version="p1", schema_version="s1",
        nutrient_per_100g=D(value))


def fills(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda sample_id, nutrient: table.get((sample_id, nutrient))


def cooking(food_id, method, *, yield_factor="0.7", energy="0.95", protein="0.9"):
    return EstimatedFactor(
        estimate_id="est-c", kind="cooking", food_id=food_id, key=method, source="llm_estimated",
        status="llm_estimated", model=MODEL, prompt_version="p1", schema_version="s1",
        yield_factor=D(yield_factor), retention={"energy_kcal": D(energy), "protein_g": D(protein)})


def lookup(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda food_id, method: table.get((food_id, mc.normalize_cooking_method(method)))


class CategoryZeroRuleTests(unittest.TestCase):
    """設定檔規則：TFDA 類別碼前綴＋營養素，每條附理由；保守，只收明顯必然為 0 者。"""

    def test_meat_seafood_and_egg_fiber_is_zero(self):
        for sample in ("I0302201", "I01101", "J0400101", "K0100101"):
            with self.subTest(sample=sample):
                self.assertIsNotNone(cz.zero_rule(sample, "dietary_fiber_g"))

    def test_fish_paste_may_contain_starch_and_is_not_covered(self):
        # 旗魚魚漿 J0600701 TFDA 膳食纖維 2.6 g：加工魚漿可能加澱粉／植物膠
        self.assertIsNone(cz.zero_rule("J0600701", "dietary_fiber_g"))

    def test_refined_oil_has_no_sodium_sugar_fiber_or_carbohydrate(self):
        for nutrient in ("sodium_mg", "sugars_g", "dietary_fiber_g", "carbohydrate_g"):
            with self.subTest(nutrient=nutrient):
                self.assertIsNotNone(cz.zero_rule("M1100101", nutrient))
        self.assertIsNone(cz.zero_rule("M1100101", "fat_g"))
        # 奶油、人造奶油（含乳糖或加鹽）不在規則內
        self.assertIsNone(cz.zero_rule("M0900201", "sodium_mg"))

    def test_refined_sugar_and_salt(self):
        for nutrient in ("protein_g", "fat_g", "dietary_fiber_g"):
            self.assertIsNotNone(cz.zero_rule("N0100101", nutrient))
            self.assertIsNotNone(cz.zero_rule("P0300101", nutrient))
        self.assertIsNone(cz.zero_rule("N0100101", "sugars_g"))  # 糖的糖量當然不是 0
        self.assertIsNone(cz.zero_rule("P0300101", "sodium_mg"))
        self.assertIsNone(cz.zero_rule("N0100401", "fat_g"))     # 黑砂糖含少量脂肪，不在規則內

    def test_unrelated_samples_and_nutrients_have_no_rule(self):
        self.assertIsNone(cz.zero_rule("P0700101", "fat_g"))     # 醬油
        self.assertIsNone(cz.zero_rule("I0302201", "sugars_g"))  # 肉的糖不一定為 0
        self.assertIsNone(cz.zero_rule(None, "fat_g"))

    def test_every_rule_has_a_reason(self):
        for rule in cz.rules():
            self.assertTrue(rule["reason"].strip(), rule["id"])
            self.assertTrue(set(rule["nutrients"]) <= set(ALL), rule["id"])


class CategoryZeroCalculatorTests(unittest.TestCase):

    def test_oil_blank_sodium_is_category_zero_and_traced(self):
        [oil] = mc.tfda_calculator([(line("大豆油", "5", consumed_as_is=True), D("5"))])
        self.assertIsNone(oil.error)
        for key in ("sodium_mg", "sugars_g", "dietary_fiber_g"):
            self.assertEqual(oil.nutrients[key], D(0), key)
        self.assertEqual(set(oil.trace["category_zero"]), {"sodium_mg", "sugars_g", "dietary_fiber_g"})
        self.assertEqual(oil.trace["category_zero"]["sodium_mg"]["sample_id"], "M1100101")
        self.assertEqual(oil.trace["estimated_factors"], [])
        self.assertFalse(oil.trace["is_estimated"])

    def test_tfda_value_wins_over_the_category_rule(self):
        # 岩鹽 P0300101：蛋白質 0.0、脂肪 0.1 都是 TFDA 實測，不經類別規則；纖維空白 → 類別 0
        [salt] = mc.tfda_calculator([(line("岩鹽", "10", consumed_as_is=True), D("10"))])
        self.assertEqual(salt.nutrients["fat_g"], D("0.01"))
        self.assertEqual(set(salt.trace["category_zero"]), {"dietary_fiber_g"})

    def test_blanks_outside_the_rules_stay_unknown(self):
        [soy] = mc.tfda_calculator([(line("醬油", "15", consumed_as_is=True), D("15"))])
        self.assertIsNone(soy.nutrients["fat_g"])
        self.assertIsNone(soy.nutrients["sugars_g"])
        self.assertEqual(soy.trace["category_zero"], {})

    def test_raw_sample_with_cooking_factor(self):
        # 豬後腿肉 I0302201（煮）：纖維空白 → 類別 0；糖空白 → 推估 0.1 g／100 g（保留率視為 1）
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-豬後腿肉", "boil")),
                                       nutrient_fills=fills(fill("I0302201", "sugars_g", "0.1")))
        [pork] = calc([(line("豬後腿肉", "200", cooking_method="煮"), D("200"))])
        self.assertIsNone(pork.error)
        self.assertEqual(pork.nutrients["energy_kcal"], D("233.7"))   # 123×2×0.95
        self.assertEqual(pork.nutrients["dietary_fiber_g"], D(0))
        self.assertEqual(pork.nutrients["sugars_g"], D("0.2"))
        self.assertIn("dietary_fiber_g", pork.trace["category_zero"])
        self.assertIn("sugars_g", pork.trace["estimated_nutrients"])

    def test_cooked_sample_fill_uses_the_cooked_weight(self):
        # 白飯 A0550601 熟樣本、熟重 150 g：糖空白 → 推估 0.2 g／100 g
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("A0550601", "sugars_g", "0.2")))
        [rice] = calc([(line("白飯", "150", weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), D("150"))])
        self.assertEqual(rice.nutrients["sugars_g"], D("0.3"))
        self.assertEqual(rice.trace["nutrient_fills"]["sugars_g"]["sample_id"], "A0550601")

    def test_fill_for_a_category_zero_nutrient_is_ignored(self):
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("M1100101", "sodium_mg", "5")))
        [oil] = calc([(line("大豆油", "10", consumed_as_is=True), D("10"))])
        self.assertEqual(oil.nutrients["sodium_mg"], D(0))
        self.assertEqual(oil.trace["estimated_factors"], [])


class MenuLikeMealTests(unittest.TestCase):
    """與使用者菜單類似（番茄炒蛋／瘦肉湯／沙茶牛肉＋白飯的調味與食材）：資料齊全時 7 種營養素都有值。"""

    def test_whole_meal_has_all_seven_nutrients(self):
        calc = mc.make_tfda_calculator(
            cooking_factors=lookup(cooking("F-豬後腿肉", "boil")),
            nutrient_fills=fills(
                fill("I0302201", "sugars_g", "0"), fill("N0100101", "sugars_g", "99.6"),
                fill("P0700101", "fat_g", "0.1"), fill("P0700101", "sugars_g", "4"),
                fill("P0700101", "dietary_fiber_g", "0"), fill("B0720101", "sugars_g", "0"),
                fill("A0550601", "sugars_g", "0.1"), fill("P1001701", "sugars_g", "3"),
                fill("P0800301", "sugars_g", "20")))
        lines = [line("豬後腿肉", "60", cooking_method="煮"), line("方糖", "3", consumed_as_is=True),
                 line("醬油", "5", consumed_as_is=True), line("大豆油", "5", consumed_as_is=True),
                 line("蠔油", "5", consumed_as_is=True), line("太白粉", "3", consumed_as_is=True),
                 line("沙茶醬", "5", consumed_as_is=True), line("岩鹽", "1", consumed_as_is=True),
                 line("白飯", "150", weight_basis="cooked")]
        result = mc.calculate_meal(lines, mc.MealTargets(Fraction(1), Fraction(500), Fraction(20)), mode="ai",
                                   calculator=calc)
        final = result["final"]
        self.assertIsNotNone(final["energy_kcal"])
        self.assertIsNotNone(final["protein_g"])
        for key in ALL[1:]:
            if key == "protein_g":
                continue
            self.assertIsNotNone(final["other_nutrients"][key], key)
        self.assertEqual(final["missing_nutrients"], {})
        # 方糖 3 g：糖 99.6×3/100；鈉來自 TFDA（2 mg／100 g）
        sugar = result["ingredients"][1]
        self.assertEqual(sugar["nutrients"]["sugars_g"], D("2.988"))
        self.assertEqual(set(sugar["trace"]["category_zero"]), {"protein_g", "fat_g", "dietary_fiber_g"})


class CheckFillTests(unittest.TestCase):
    """store.check_fill：LLM 批次與人工覆寫共用的檢查（每 100 g）。"""

    def test_new_nutrients_within_range_are_accepted(self):
        self.assertIsNone(store.check_fill("P0700101", "sugars_g", D("5")))
        self.assertIsNone(store.check_fill("P0700101", "dietary_fiber_g", D("0.5")))
        self.assertIsNone(store.check_fill("P0700101", "fat_g", D("0.2")))
        self.assertIsNone(store.check_fill("M0100101", "sodium_mg", D("30000")))   # 牛油(未精煉) 鈉空白

    def test_out_of_range_values_are_refused(self):
        self.assertEqual(store.check_fill("M0100101", "sodium_mg", D("40001")), "OUT_OF_RANGE")
        self.assertEqual(store.check_fill("P0700101", "sugars_g", D("101")), "OUT_OF_RANGE")
        self.assertEqual(store.check_fill("P0700101", "sugars_g", D("-1")), "OUT_OF_RANGE")

    def test_sugar_cannot_exceed_carbohydrate(self):
        # 醬油碳水 14.7 g
        self.assertEqual(store.check_fill("P0700101", "sugars_g", D("20")), "OUT_OF_RANGE")

    def test_sugar_vs_carbohydrate_is_only_checked_when_filling_one_of_them(self):
        # decisions #56：培根 R5100801 的 TFDA 碳水 0、糖 1.4（樣本本身不一致）；補膳食纖維不應因此被拒
        self.assertIsNone(store.check_fill("R5100801", "dietary_fiber_g", D("0")))

    def test_violations_name_the_out_of_range_fields(self):
        self.assertEqual(store.fill_violations("P0700101", "sugars_g", D("20")), ["sugars_g>carbohydrate_g"])
        self.assertEqual(store.fill_violations("M0100101", "sodium_mg", D("40001")), ["sodium_mg"])

    def test_macros_cannot_exceed_100_g_by_much(self):
        # 醬油：碳水 14.7＋蛋白質 7.8＋脂肪 90 > 105 g
        self.assertEqual(store.check_fill("P0700101", "fat_g", D("90")), "OUT_OF_RANGE")

    def test_category_zero_and_tfda_values_are_refused(self):
        self.assertIn("category", store.check_fill("M1100101", "sodium_mg", D("0")))
        self.assertIn("TFDA wins", store.check_fill("P0700101", "sodium_mg", D("100")))


class FakeResponder:
    def __init__(self):
        self.calls = []

    def respond(self, **kwargs):
        raise AssertionError("dry-run must not call the model")


@requires_db
class SevenNutrientScanTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
        bundle = parse_recipe_workbook(build(Path(folder.name) / "book.xlsx", [
            recipe("R001", "沙茶肉片", ingredients=[
                ingredient("FOOD900001", "豬後腿肉", 200), ingredient("FOOD900002", "醬油", 10),
                ingredient("FOOD900003", "大豆油", 10), ingredient("FOOD900004", "方糖", 5)])]))
        with session_factory()() as session, session.begin():
            import_recipe_bundle(session, bundle)

    def fill_gaps(self):
        with session_factory()() as db:
            return [g for g in batch.scan(db) if g.kind == "nutrient_fill"]

    def test_scan_covers_seven_nutrients_and_skips_category_zero(self):
        keys = {(g.food_id, g.key) for g in self.fill_gaps()}
        recipe_keys = {k for k in keys if k[0] in ("I0302201", "P0700101", "M1100101", "N0100101")}
        self.assertEqual(recipe_keys, {("I0302201", "sugars_g"), ("P0700101", "fat_g"), ("P0700101", "sugars_g"),
                                       ("P0700101", "dietary_fiber_g"), ("N0100101", "sugars_g")})

    def test_scan_includes_staple_samples(self):
        gaps = {(g.food_id, g.key): g for g in self.fill_gaps()}
        rice = gaps[("A0550601", "sugars_g")]
        self.assertIn("白飯", rice.input_snapshot["staple_options"])
        self.assertEqual(rice.input_snapshot["unit"], "g")

    def test_dry_run_reports_gap_count_and_estimated_cost(self):
        responder = FakeResponder()
        report = batch.run(session_factory(), responder=responder, kinds=("nutrient_fill",), dry_run=True)
        self.assertEqual(report["by_kind"]["nutrient_fill"]["pending"], report["pending"])
        self.assertGreaterEqual(report["pending"], 5)
        self.assertGreater(report["estimated_max_cost_twd"], D(0))
        self.assertEqual(responder.calls, [])


if __name__ == "__main__":
    unittest.main()
