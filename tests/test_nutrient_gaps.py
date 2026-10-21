"""decisions #32–#35：TFDA 必要營養缺值、水、食鹽對照、熟食克數。

測試的介面（seams）：
1. make_tfda_calculator(...)：水為已知 0（#33）、熟食以熟重計（#35）、套用缺值營養推估（#32）；
2. calculate_meal：結果標示「LLM 推估」的營養；
3. estimation.store／CLI（kind=nutrient_fill）：覆寫、覆核、TFDA 有值時拒絕；
4. estimation.batch：掃描合格食譜用到且樣本必要營養空白的組合、Structured Outputs、合理範圍、
   T10 預留／結算、冪等、dry-run；水不產生 TFDA 對照缺口。
絕不呼叫真實 OpenAI：responder 一律為假物件。
"""
import json
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.estimation import batch, store
from app.meal_calc.types import EstimatedFactor
from app.services import meal_calculation as mc
from app.services import tfda_mapping
from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from recipe_workbook import build, ingredient, recipe

D = Decimal
MODEL = "gpt-5.4-mini-2026-03-17"
WATER = "FOOD000119"


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name,
                             grams_per_n=None if grams is None else D(grams), **kw)


def fill(sample_id, nutrient, value, *, source="llm_estimated", status="llm_estimated"):
    return EstimatedFactor(
        estimate_id=f"fill-{sample_id}-{nutrient}", kind="nutrient_fill", food_id=sample_id, key=nutrient,
        source=source, status=status, model=MODEL if source == "llm_estimated" else None,
        prompt_version="p1" if source == "llm_estimated" else None,
        schema_version="s1" if source == "llm_estimated" else None, nutrient_per_100g=D(value))


def fills(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda sample_id, nutrient: table.get((sample_id, nutrient))


def cooking(food_id, method, *, yield_factor="1", energy="1", protein="1"):
    return EstimatedFactor(
        estimate_id="est-1", kind="cooking", food_id=food_id, key=method, source="llm_estimated",
        status="llm_estimated", model=MODEL, prompt_version="p1", schema_version="s1",
        yield_factor=D(yield_factor), retention={"energy_kcal": D(energy), "protein_g": D(protein)})


def lookup(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda food_id, method: table.get((food_id, mc.normalize_cooking_method(method)))


def mapping(food_id, sample_id):
    return EstimatedFactor(estimate_id="map-1", kind="tfda_mapping", food_id=food_id, key="tfda", source="human",
                           status="overridden", model=None, prompt_version=None, schema_version=None,
                           tfda_sample_id=sample_id)


class WaterTests(unittest.TestCase):
    """#33：水的營養為已知 0，不需 TFDA 對照。"""

    def test_water_by_food_id_is_known_zero_and_traceable(self):
        [water] = mc.tfda_calculator([(line("水", "300", food_id=WATER, cooking_method="煮"), D("300"))])
        self.assertIsNone(water.error)
        self.assertEqual((water.nutrients["energy_kcal"], water.nutrients["protein_g"]), (D(0), D(0)))
        self.assertEqual(water.trace["nutrition_basis"], mc.WATER_KNOWN_ZERO)
        self.assertIsNone(water.trace["sample_id"])

    def test_water_by_name_with_unknown_food_id_is_known_zero(self):
        [water] = mc.tfda_calculator([(line("滾水", "100", food_id="FOOD-X"), D("100"))])
        self.assertEqual(water.nutrients["energy_kcal"], D(0))
        self.assertIsNone(water.error)

    def test_meal_with_water_is_complete(self):
        lines = [line("白飯", "100", weight_basis="cooked"), line("水", "200", food_id=WATER)]
        result = mc.calculate_meal(lines, mc.MealTargets(Fraction(1), Fraction(183), Fraction("3.1")), mode="ai",
                                   calculator=mc.tfda_calculator)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        water = result["ingredients"][1]
        self.assertFalse(water["llm_estimated"])
        self.assertEqual(water["trace"]["nutrition_basis"], mc.WATER_KNOWN_ZERO)


class CookedFoodWeightTests(unittest.TestCase):
    """#35：對照到熟樣本、且食材本身就是熟食（白米飯）時，工作簿克數視為熟重。"""

    def calc(self, ln, resolver=None):
        return mc.make_tfda_calculator(tfda_resolver=resolver)([(ln, ln.grams_per_n)])[0]

    def test_cooked_rice_from_workbook_is_cooked_weight(self):
        resolver = tfda_mapping.TfdaResolver(mappings={"F-白米飯": mapping("F-白米飯", "A0550601")})
        rice = self.calc(line("白米飯", "200", cooking_method="炒",
                              weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), resolver)
        self.assertIsNone(rice.error)
        # 白飯 A0550601：183 kcal、3.1 g／100 g × 200 g 熟重
        self.assertEqual((rice.nutrients["energy_kcal"], rice.nutrients["protein_g"]), (D("366"), D("6.2")))
        self.assertEqual((rice.trace["weight_basis"], rice.trace["weight_basis_source"]),
                         ("cooked", mc.COOKED_FOOD_SAMPLE))

    def test_exact_name_white_rice_from_workbook_is_cooked_weight(self):
        rice = self.calc(line("白飯", "100", weight_basis_source=mc.WORKBOOK_RAW_CONVENTION))
        self.assertEqual(rice.nutrients["energy_kcal"], D("183"))

    def test_meal_row_reports_the_effective_basis(self):
        result = mc.calculate_meal([line("白飯", "100", weight_basis_source=mc.WORKBOOK_RAW_CONVENTION)],
                                   mc.MealTargets(Fraction(1), Fraction(183), Fraction("3.1")), mode="ai",
                                   calculator=mc.tfda_calculator)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["ingredients"][0]["weight_basis"], "cooked")

    def test_raw_food_name_mapped_to_a_cooked_sample_stays_raw(self):
        resolver = tfda_mapping.TfdaResolver(mappings={"F-米": mapping("F-米", "A0550601")})
        rice = self.calc(line("米", "100", weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), resolver)
        self.assertEqual(rice.error, "cooked_food_requires_cooked_weight_basis")

    def test_explicit_raw_basis_is_not_overridden(self):
        rice = self.calc(line("白飯", "100", weight_basis="raw", weight_basis_source=mc.RECIPE_FIELD))
        self.assertEqual(rice.error, "cooked_food_requires_cooked_weight_basis")


class NutrientFillCalculatorTests(unittest.TestCase):
    """#32：TFDA 樣本必要營養空白時套用快取推估；TFDA 有值一律優先。"""

    def test_blank_protein_is_filled_and_marked_llm_estimated(self):
        # 麥芽糖 N0200101：325 kcal／100 g，蛋白質空白（不在 #45 類別 0 規則內）
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("N0200101", "protein_g", "0.1")))
        [sugar] = calc([(line("麥芽糖", "10", consumed_as_is=True), D("10"))])
        self.assertIsNone(sugar.error)
        self.assertEqual(sugar.nutrients["energy_kcal"], D("32.5"))
        self.assertEqual(sugar.nutrients["protein_g"], D("0.01"))
        self.assertIn("protein_g", sugar.trace["estimated_nutrients"])
        [marker] = sugar.trace["estimated_factors"]
        self.assertEqual((marker["kind"], marker["key"], marker["llm_estimated"]), ("nutrient_fill", "protein_g", True))

    def test_without_a_fill_protein_stays_unknown(self):
        [sugar] = mc.tfda_calculator([(line("麥芽糖", "10", consumed_as_is=True), D("10"))])
        self.assertIsNone(sugar.nutrients["protein_g"])

    def test_seasoning_fill_ignores_cooking_factors(self):
        # decisions #49(a)：香油 P0900201（unspecified、P 調味料）以原樣組成計算，不套烹調保留率：
        # 熱量 883×10/100；蛋白質空白由推估 0.2×10/100 補上
        calc = mc.make_tfda_calculator(
            cooking_factors=lookup(cooking("F-香油", "stir", energy="0.9", protein="0.5")),
            nutrient_fills=fills(fill("P0900201", "protein_g", "0.2")))
        [oil] = calc([(line("香油", "10", cooking_method="炒"), D("10"))])
        self.assertIsNone(oil.error)
        self.assertEqual(oil.nutrients["energy_kcal"], D("88.3"))
        self.assertEqual(oil.nutrients["protein_g"], D("0.02"))
        self.assertEqual({m["kind"] for m in oil.trace["estimated_factors"]}, {"nutrient_fill"})

    def test_tfda_value_always_wins_over_a_fill(self):
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("M1100101", "energy_kcal", "1")))
        [oil] = calc([(line("大豆油", "5", consumed_as_is=True), D("5"))])
        self.assertEqual(oil.nutrients["energy_kcal"], D("44.2"))
        self.assertEqual(oil.trace["estimated_factors"], [])

    def test_human_fill_is_not_labelled_llm(self):
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("N0200101", "protein_g", "0", source="human",
                                                                 status="overridden")))
        [sugar] = calc([(line("麥芽糖", "10", consumed_as_is=True), D("10"))])
        self.assertEqual(sugar.nutrients["protein_g"], D("0"))
        self.assertFalse(sugar.trace["estimated_factors"][0]["llm_estimated"])

    def test_meal_result_lists_the_estimated_nutrient(self):
        calc = mc.make_tfda_calculator(nutrient_fills=fills(fill("N0200101", "protein_g", "0")))
        result = mc.calculate_meal([line("麥芽糖", "10", consumed_as_is=True), line("白飯", "100", weight_basis="cooked")],
                                   mc.MealTargets(Fraction(1), Fraction("215.5"), Fraction("3.1")), mode="ai",
                                   calculator=calc)
        self.assertTrue(result["adoptable"], result["block_reasons"])
        self.assertEqual(result["llm_estimates"]["food_ids"], ["F-麥芽糖"])
        self.assertIn("protein_g", result["llm_estimates"]["nutrients"])
        sugar = result["ingredients"][0]
        self.assertTrue(sugar["llm_estimated"])
        self.assertEqual(sugar["estimated_nutrients"], ["protein_g"])


class FakeResponder:
    """假的 OpenAI Responses 轉接器：nutrient_fill 依樣本與營養素回傳數值。"""

    def __init__(self, values=None, default=0.1):
        self.values = values or {}
        self.default = default
        self.calls = []

    def respond(self, **kwargs):
        from app.services.llm_client import StructuredResponse
        self.calls.append(kwargs)
        data = json.loads(kwargs["input_text"])
        if kwargs["schema_name"] != "nutrient_fill_estimate":
            body = {"tfda_sample_id": "NONE", "reason": "x"}
        else:
            outcome = self.values.get((data["sample_id"], data["nutrient"]), self.default)
            if isinstance(outcome, StructuredResponse):
                return outcome, {"input_tokens": 300, "output_tokens": 40}
            body = {"value_per_100g": outcome, "assumptions": ["糖幾乎不含蛋白質"]}
        return StructuredResponse("completed", output_text=json.dumps(body, ensure_ascii=False)), \
            {"input_tokens": 300, "output_tokens": 40}


def import_recipes(folder, recipes):
    from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
    bundle = parse_recipe_workbook(build(Path(folder) / "book.xlsx", recipes))
    with session_factory()() as session, session.begin():
        import_recipe_bundle(session, bundle)


@requires_db
class NutrientFillBatchTests(unittest.TestCase):
    """python -m app.estimation run --kind nutrient_fill"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        import_recipes(self.folder.name, [
            recipe("R001", "冰糖滷蛋", servings="2人份", ingredients=[
                ingredient("FOOD000039", "大蒜", 10),
                ingredient("FOOD000301", "冰糖", 20),
                ingredient("FOOD000302", "香油", 5),
                ingredient(WATER, "水", 300),
            ])])

    def run_batch(self, responder, **kw):
        return batch.run(session_factory(), responder=responder, kinds=("nutrient_fill",), **kw)

    def rows(self, **kw):
        with session_factory()() as db:
            return store.list_estimates(db, kind="nutrient_fill", **kw)

    def reservations(self):
        from sqlalchemy import select
        from app.db.models import UsageReservation
        with session_factory()() as db:
            return db.scalars(select(UsageReservation)).all()

    def test_scan_lists_blank_required_nutrients_of_used_samples(self):
        with session_factory()() as db:
            gaps = batch.scan(db)
        fill_keys = sorted((g.food_id, g.key) for g in gaps if g.kind == "nutrient_fill")
        # 香油 P0900201 蛋白質空白（糖、纖維、鈉為 #45 類別 0）；冰糖 N0100201 的蛋白質、脂肪、纖維為類別 0；
        # 大蒜皆有值；水不需對照；主食白飯 A0550601 的糖空白（#45 主食樣本一併掃描）
        self.assertEqual(fill_keys, [("A0550601", "sugars_g"), ("P0900201", "protein_g")])
        self.assertNotIn(WATER, {g.food_id for g in gaps if g.kind == "tfda_mapping"})
        oil = next(g for g in gaps if g.food_id == "P0900201")
        self.assertEqual(oil.input_snapshot["known_per_100g"]["energy_kcal"], 883)
        self.assertEqual(oil.input_snapshot["recipe_ids"], ["R001"])

    def test_dry_run_reports_without_calling_or_writing(self):
        responder = FakeResponder()
        report = self.run_batch(responder, dry_run=True)
        self.assertEqual(report["pending"], 2)
        self.assertEqual((responder.calls, self.rows(), self.reservations()), ([], [], []))

    def test_values_are_validated_stored_billed_and_used_by_generation(self):
        responder = FakeResponder({("P0900201", "protein_g"): 150})
        report = self.run_batch(responder)
        self.assertEqual(report["estimated"], 1)
        self.assertEqual([f["reason"] for f in report["failed"]], ["OUT_OF_RANGE"])
        call = responder.calls[0]
        self.assertEqual(call["model"], MODEL)
        self.assertFalse(call["schema"]["additionalProperties"])
        [row] = self.rows()
        self.assertEqual((row["food_id"], row["key"], row["nutrient_per_100g"], row["status"], row["model"]),
                         ("A0550601", "sugars_g", D("0.1"), "llm_estimated", MODEL))
        self.assertEqual(row["prompt_version"], batch.FILL_PROMPT_VERSION)
        reservations = self.reservations()
        self.assertEqual(len(reservations), 2)
        self.assertTrue(all(r.status == "settled" and r.purpose == batch.FILL_PURPOSE for r in reservations))
        with session_factory()() as db:
            calc = mc.cached_factor_calculator(db)
        [rice] = calc([(line("白飯", "150", weight_basis="cooked"), D("150"))])
        self.assertEqual(rice.nutrients["sugars_g"], D("0.15"))
        self.assertTrue(rice.trace["estimated_factors"][0]["llm_estimated"])

    def test_rerun_is_idempotent(self):
        self.run_batch(FakeResponder())
        again = FakeResponder()
        report = self.run_batch(again)
        self.assertEqual((again.calls, report["pending"]), ([], 0))

    def test_refusal_and_bad_format_are_not_written(self):
        from app.services.llm_client import StructuredResponse
        report = self.run_batch(FakeResponder({("A0550601", "sugars_g"): StructuredResponse("refused", refusal="no"),
                                               ("P0900201", "protein_g"): -3}))
        self.assertEqual({(f["food_id"], f["reason"]) for f in report["failed"]},
                         {("A0550601", "MODEL_REFUSED"), ("P0900201", "OUT_OF_RANGE")})
        # decisions #56：失敗報告記錄超界欄位與模型原始值
        [bad] = [f for f in report["failed"] if f["reason"] == "OUT_OF_RANGE"]
        self.assertEqual((bad["out_of_range_fields"], bad["values"]), (["protein_g"], {"protein_g": D("-3")}))
        self.assertEqual(self.rows(), [])


@requires_db
class NutrientFillCommandLineTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()

    def cli(self, *argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from app.estimation import __main__ as cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), factory=session_factory(), responder=None)
        return code, out.getvalue(), err.getvalue()

    def test_override_list_approve_and_reject(self):
        code, _, err = self.cli("override", "--kind", "nutrient_fill", "--food", "N0200101", "--key", "protein_g",
                                "--value", "0", "--reviewer", "營養師A", "--note", "糖不含蛋白質")
        self.assertEqual(code, 0, err)
        with session_factory()() as db:
            factor = store.load_snapshot(db).nutrient_fill("N0200101", "protein_g")
        self.assertEqual((factor.source, factor.nutrient_per_100g), ("human", D("0")))
        code, out, _ = self.cli("list", "--kind", "nutrient_fill")
        [row] = json.loads(out)
        self.assertEqual(self.cli("reject", row["id"], "--reviewer", "營養師B")[0], 0)
        with session_factory()() as db:
            self.assertIsNone(store.load_snapshot(db).nutrient_fill("N0200101", "protein_g"))

    def test_override_is_refused_when_tfda_has_a_value_or_value_is_implausible(self):
        cases = [("E2100101", "protein_g", "1"),   # 大蒜蛋白質 TFDA 有值
                 ("N0100101", "protein_g", "-1"),
                 ("P0600101", "protein_g", "5"),   # 米醋 10 kcal：5 g 蛋白質需 20 kcal，不一致
                 ("N0100101", "fat_g", "0"),       # #45：精製糖的脂肪為類別 0，不需推估
                 ("NOPE", "protein_g", "0")]
        for sample, key, value in cases:
            with self.subTest(sample=sample, key=key):
                code, _, err = self.cli("override", "--kind", "nutrient_fill", "--food", sample, "--key", key,
                                        "--value", value, "--reviewer", "營養師A")
                self.assertEqual(code, 1)
                self.assertTrue(err)
        self.assertEqual(json.loads(self.cli("list", "--kind", "nutrient_fill")[1]), [])


if __name__ == "__main__":
    unittest.main()
