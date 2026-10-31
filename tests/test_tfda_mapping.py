"""decisions #28：食譜食材 → TFDA 樣本對照。

解析順序：精確名稱（T04）→ 工作簿別名／已確認同義詞 → 已審核對照（approved／overridden）
→ LLM 對照推薦（llm_mapped）→ 否則 ingredient_not_found。

測試的介面（seams）：
1. TfdaResolver.resolve(food_id, name)：純解析順序（真實 T04 TFDA 資料）；
2. make_tfda_calculator(tfda_resolver=...)：對照後的營養與 trace 來源；
3. calculate_meal／meal_results：結果與畫面標示「LLM 對照」；
4. estimation.batch／CLI（kind=tfda_mapping）：缺口掃描、候選清單、Structured Outputs、T10 預留／結算、
   冪等、dry-run、覆核；
5. #19 多種烹調方式：組合鍵查快取並算出營養（含 TFDA 狀態 unspecified 的大蒜）。
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


def mapping(food_id, sample_id, *, status="llm_estimated", source="llm_estimated", estimate_id="map-1"):
    return EstimatedFactor(
        estimate_id=estimate_id, kind="tfda_mapping", food_id=food_id, key=tfda_mapping.MAPPING_KEY,
        source=source, status=status, model=MODEL if source == "llm_estimated" else None,
        prompt_version="p1" if source == "llm_estimated" else None,
        schema_version="s1" if source == "llm_estimated" else None, tfda_sample_id=sample_id)


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name,
                             grams_per_n=None if grams is None else D(grams), **kw)


class ResolverOrderTests(unittest.TestCase):
    """解析順序與來源標示（純計算、真實 TFDA 資料）。"""

    def test_exact_name_wins_and_is_marked_exact(self):
        resolver = tfda_mapping.TfdaResolver(mappings={"F1": mapping("F1", "E2100201")})
        res = resolver.resolve("F1", "大蒜")
        self.assertEqual((res.source, res.sample_id, res.tfda_name), ("exact", "E2100101", "大蒜"))
        self.assertFalse(res.llm_mapped)

    def test_workbook_alias_is_used_when_the_name_is_unknown(self):
        # 「蒜仔頭」不是 TFDA 名稱；工作簿別名「蒜頭」解得到 E2100101
        resolver = tfda_mapping.TfdaResolver(aliases={"F1": ["蔬菜", "蒜頭"]})
        res = resolver.resolve("F1", "蒜仔頭X")
        self.assertEqual((res.source, res.sample_id, res.via_name), ("alias", "E2100101", "蒜頭"))

    def test_confirmed_synonym_names_are_used_after_aliases(self):
        resolver = tfda_mapping.TfdaResolver(synonyms={"F1": ["大蒜"]})
        res = resolver.resolve("F1", "蒜仔頭X")
        self.assertEqual((res.source, res.sample_id), ("synonym", "E2100101"))

    def test_human_mapping_beats_llm_mapping_source_label(self):
        human = tfda_mapping.TfdaResolver(mappings={"F1": mapping("F1", "E5600101", status="overridden",
                                                                    source="human")})
        res = human.resolve("F1", "空心菜")
        self.assertEqual((res.source, res.sample_id), ("human", "E5600101"))
        self.assertFalse(res.llm_mapped)
        approved = tfda_mapping.TfdaResolver(mappings={"F1": mapping("F1", "E5600101", status="approved")})
        self.assertEqual(approved.resolve("F1", "空心菜").source, "human")

    def test_unreviewed_llm_mapping_is_marked_llm_mapped(self):
        resolver = tfda_mapping.TfdaResolver(mappings={"F1": mapping("F1", "E23001")})
        res = resolver.resolve("F1", "青蔥")
        self.assertEqual((res.source, res.sample_id), ("llm_mapped", "E23001"))
        self.assertTrue(res.llm_mapped)
        self.assertEqual(res.estimate.model, MODEL)

    def test_rejected_or_no_match_or_unknown_sample_resolves_to_nothing(self):
        for factor in (mapping("F1", "E23001", status="rejected"), mapping("F1", None),
                       mapping("F1", "ZZZ-not-a-sample")):
            with self.subTest(factor):
                self.assertIsNone(tfda_mapping.TfdaResolver(mappings={"F1": factor}).resolve("F1", "青蔥"))
        self.assertIsNone(tfda_mapping.TfdaResolver().resolve("F1", "空心菜"))


class MappedNutritionTests(unittest.TestCase):
    """calculator 以對照到的 TFDA 樣本計算營養，trace 與結果標示來源。"""

    def test_llm_mapped_food_gets_nutrition_from_the_chosen_sample(self):
        # 青蔥平均值 E23001；以 consumed_as_is（不需烹調換算）驗證營養值來自該樣本
        resolver = tfda_mapping.TfdaResolver(mappings={"F-青蔥": mapping("F-青蔥", "E23001")})
        calc = mc.make_tfda_calculator(tfda_resolver=resolver)
        [row] = calc([(line("青蔥", "50", consumed_as_is=True), D("50"))])
        self.assertIsNone(row.error)
        energy = tfda_mapping.sample("E23001")["nutrition_per_100g"]["energy_kcal"]
        self.assertEqual(row.nutrients["energy_kcal"], D(str(energy)) / 2)
        self.assertEqual(row.trace["sample_id"], "E23001")
        self.assertEqual(row.trace["tfda_mapping"]["source"], "llm_mapped")
        self.assertEqual(row.trace["tfda_mapping"]["input_name"], "青蔥")
        self.assertTrue(row.trace["tfda_mapping"]["llm_mapped"])

    def test_without_resolver_unknown_names_stay_not_found(self):
        [row] = mc.tfda_calculator([(line("空心菜", "50", consumed_as_is=True), D("50"))])
        self.assertEqual(row.error, "ingredient_not_found")
        self.assertIsNone(row.nutrients["energy_kcal"])

    def test_exact_match_is_recorded_in_trace(self):
        [row] = mc.tfda_calculator([(line("白飯", "100", weight_basis="cooked"), D("100"))])
        self.assertEqual(row.trace["tfda_mapping"]["source"], "exact")

    def test_meal_result_labels_llm_mapped_ingredients(self):
        from app.services import meal_results
        resolver = tfda_mapping.TfdaResolver(mappings={"F-青蔥": mapping("F-青蔥", "E23001")})
        calc = mc.make_tfda_calculator(tfda_resolver=resolver)
        result = mc.calculate_meal([line("青蔥", "50", consumed_as_is=True, dish_id="R1"),
                                    line("白飯", "100", weight_basis="cooked", dish_id="R1")],
                                   mc.MealTargets(Fraction(1), Fraction(200), Fraction(5)), mode="ai",
                                   calculator=calc)
        scallion, rice = result["ingredients"]
        self.assertEqual((scallion["tfda_mapping_source"], scallion["llm_mapped"]), ("llm_mapped", True))
        self.assertEqual((rice["tfda_mapping_source"], rice["llm_mapped"]), ("exact", False))
        self.assertEqual(result["llm_mappings"], {"used": True, "food_ids": ["F-青蔥"]})
        payload = {"calculation": result, "dishes": [{"standard_dish_id": "D1", "dish_name": "蔥飯",
                                                      "source": "database", "recipe_id": "R1"}]}
        [dish] = meal_results._dishes(payload)
        view = {i["name"]: i for i in dish["ingredients"]}
        self.assertEqual(view["青蔥"]["mapping_label"], "LLM 對照")
        self.assertEqual(view["青蔥"]["tfda_sample_id"], "E23001")
        self.assertIsNone(view["白飯"]["mapping_label"])
        self.assertEqual(meal_results._nutrition(payload)["llm_mappings"],
                         {"used": True, "food_ids": ["F-青蔥"], "label": "LLM 對照"})


def cooking(food_id, method, *, yield_factor="0.9", energy="0.95", protein="0.9"):
    return EstimatedFactor(
        estimate_id="est-1", kind="cooking", food_id=food_id, key=method, source="llm_estimated",
        status="llm_estimated", model=MODEL, prompt_version="p1", schema_version="s1",
        yield_factor=D(yield_factor), retention={"energy_kcal": D(energy), "protein_g": D(protein)})


def lookup(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda food_id, method: table.get((food_id, mc.normalize_cooking_method(method)))


class SeveralCookingMethodsTests(unittest.TestCase):
    """decisions #19：食譜列多種烹調方式時，以組合鍵查快取；大蒜（TFDA 狀態 unspecified）與高麗菜都要算得出。"""

    def test_garlic_and_cabbage_with_several_methods_use_the_combined_key(self):
        key = mc.combined_method_key(["炒", "煮"])
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-大蒜", key), cooking("F-高麗菜", key)))
        garlic_line = line("大蒜", "10", cooking_methods=("炒", "煮"))
        cabbage_line = line("高麗菜", "200", cooking_methods=("炒", "煮"))
        garlic, cabbage = calc([(garlic_line, D("10")), (cabbage_line, D("200"))])
        # 大蒜 E2100101：122 kcal、6.7 g／100 g × 10 g × 保留率 0.95／0.9
        self.assertEqual(garlic.nutrients["energy_kcal"], D("11.59"))
        self.assertEqual(garlic.nutrients["protein_g"], D("0.603"))
        self.assertEqual(garlic.trace["sample_id"], "E2100101")
        self.assertEqual(garlic.trace["estimated_factors"][0]["key"], key)
        self.assertIsNone(garlic.error)
        # 高麗菜 → 甘藍平均值 E30001：23 kcal／100 g × 200 g × 0.95
        self.assertEqual(cabbage.nutrients["energy_kcal"], D("43.7"))
        self.assertIsNotNone(cabbage.nutrients["protein_g"])
        self.assertIsNone(cabbage.trace["missing_reason"])

    def test_garlic_with_a_single_method_uses_the_cached_factor_too(self):
        # T04 的烹調管線不接受 unspecified 狀態（回報 ingredient_not_found），T12 以生樣品套保留率
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-大蒜", "stir")))
        [garlic] = calc([(line("大蒜", "10", cooking_method="炒"), D("10"))])
        self.assertIsNone(garlic.error)
        self.assertEqual(garlic.nutrients["energy_kcal"], D("11.59"))

    def test_mapped_food_uses_mapped_sample_for_cooking_factors(self):
        key = mc.combined_method_key(["炒", "煮"])
        resolver = tfda_mapping.TfdaResolver(mappings={"F-空心菜": mapping("F-空心菜", "E5600101")})
        calc = mc.make_tfda_calculator(cooking_factors=lookup(cooking("F-空心菜", key)), tfda_resolver=resolver)
        [row] = calc([(line("空心菜", "100", cooking_methods=("炒", "煮")), D("100"))])
        self.assertIsNone(row.error)
        energy = tfda_mapping.sample("E5600101")["nutrition_per_100g"]["energy_kcal"]
        self.assertEqual(row.nutrients["energy_kcal"], D(str(energy)) * D("0.95"))
        self.assertEqual(row.trace["tfda_mapping"]["source"], "llm_mapped")


class CandidateTests(unittest.TestCase):
    """候選 TFDA 樣本：以字串相似度／別名縮小，模型只能從清單選。"""

    def test_candidates_contain_the_obvious_samples(self):
        cases = {"空心菜": "E5600101", "青蔥": "E23001", "辣椒": "E75006", "食鹽": "P0300101"}
        for name, expected in cases.items():
            with self.subTest(name):
                ids = [c["sample_id"] for c in tfda_mapping.candidates([name])]
                self.assertIn(expected, ids)
                self.assertLessEqual(len(ids), tfda_mapping.MAX_CANDIDATES)

    def test_nonsense_has_no_candidates(self):
        self.assertEqual(tfda_mapping.candidates(["ＸＹＺ"]), [])


class FakeResponder:
    """假的 OpenAI Responses 轉接器：tfda_mapping 依食材名稱回傳選擇。"""

    def __init__(self, choices=None, default="NONE"):
        self.choices = choices or {}
        self.default = default
        self.calls = []

    def respond(self, **kwargs):
        from app.services.llm_client import StructuredResponse
        self.calls.append(kwargs)
        data = json.loads(kwargs["input_text"])
        choice = self.choices.get(data["food_name"], self.default)
        if isinstance(choice, StructuredResponse):
            return choice, {"input_tokens": 400, "output_tokens": 50}
        body = {"tfda_sample_id": choice, "reason": "最常見的生鮮樣本"}
        return StructuredResponse("completed", output_text=json.dumps(body, ensure_ascii=False)), \
            {"input_tokens": 400, "output_tokens": 50}


@requires_db
class MappingBatchTests(unittest.TestCase):
    """python -m app.estimation run --kind tfda_mapping"""

    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        from app.services.recipe_import import import_recipe_bundle, parse_recipe_workbook
        bundle = parse_recipe_workbook(build(Path(self.folder.name) / "book.xlsx", [
            recipe("R001", "炒空心菜", servings="2人份", ingredients=[
                ingredient("FOOD000509", "空心菜", 300),
                ingredient("FOOD000598", "青蔥", 20),
                ingredient("FOOD000039", "大蒜", 10),
            ])]))
        with session_factory()() as session, session.begin():
            import_recipe_bundle(session, bundle)

    def run_batch(self, responder, **kw):
        return batch.run(session_factory(), responder=responder, kinds=("tfda_mapping",), **kw)

    def rows(self, **kw):
        with session_factory()() as db:
            return store.list_estimates(db, kind="tfda_mapping", **kw)

    def reservations(self):
        from sqlalchemy import select
        from app.db.models import UsageReservation
        with session_factory()() as db:
            return db.scalars(select(UsageReservation)).all()

    def test_scan_lists_only_unresolved_foods_with_candidates(self):
        with session_factory()() as db:
            gaps = [g for g in batch.scan(db) if g.kind == "tfda_mapping"]
        self.assertEqual(sorted(g.food_id for g in gaps), ["FOOD000509", "FOOD000598"])  # 大蒜精確對到
        greens = next(g for g in gaps if g.food_id == "FOOD000509")
        self.assertIn("E5600101", [c["sample_id"] for c in greens.input_snapshot["candidates"]])
        self.assertEqual(greens.input_snapshot["recipe_ids"], ["R001"])

    def test_dry_run_reports_gaps_without_calling_or_writing(self):
        responder = FakeResponder()
        report = self.run_batch(responder, dry_run=True)
        self.assertEqual(report["pending"], 2)
        self.assertEqual(responder.calls, [])
        self.assertEqual(self.rows(), [])

    def test_choice_is_validated_stored_billed_and_used_by_generation(self):
        responder = FakeResponder({"空心菜": "E5600101", "青蔥": "NOT-IN-LIST"})
        report = self.run_batch(responder)
        self.assertEqual(report["estimated"], 1)
        self.assertEqual([f["reason"] for f in report["failed"]], ["CHOICE_NOT_IN_CANDIDATES"])
        call = responder.calls[0]
        self.assertEqual(call["model"], MODEL)
        self.assertIn("NONE", call["schema"]["properties"]["tfda_sample_id"]["enum"])
        [row] = self.rows()
        self.assertEqual((row["food_id"], row["tfda_sample_id"], row["status"], row["model"]),
                         ("FOOD000509", "E5600101", "llm_estimated", MODEL))
        self.assertEqual(row["prompt_version"], batch.MAPPING_PROMPT_VERSION)
        self.assertTrue(all(r.status == "settled" for r in self.reservations()))
        self.assertEqual(len(self.reservations()), 2)
        with session_factory()() as db:
            lines = mc.load_recipe_lines(db, "R001")
            calc = mc.cached_factor_calculator(db)
        greens = calc([(lines[0], lines[0].grams_per_n)])[0]
        self.assertEqual(greens.trace["sample_id"], "E5600101")
        self.assertEqual(greens.trace["tfda_mapping"]["source"], "llm_mapped")

    def test_none_answer_is_recorded_so_reruns_are_idempotent(self):
        self.run_batch(FakeResponder(default="NONE"))
        self.assertEqual({r["tfda_sample_id"] for r in self.rows()}, {None})
        again = FakeResponder()
        report = self.run_batch(again)
        self.assertEqual((again.calls, report["pending"]), ([], 0))
        with session_factory()() as db:
            self.assertIsNone(tfda_mapping.load_resolver(db).resolve("FOOD000509", "空心菜"))

    def test_cooking_gap_appears_for_a_mapped_food(self):
        from sqlalchemy import text
        with session_factory()() as db, db.begin():
            db.execute(text("""UPDATE recipes SET cooking_methods = '["炒", "煮"]'"""))
        with session_factory()() as db:
            before = {(g.kind, g.food_id) for g in batch.scan(db)}
        self.assertNotIn(("cooking", "FOOD000509"), before)
        self.assertIn(("cooking", "FOOD000039"), before)  # 大蒜（unspecified）也要能推估
        self.run_batch(FakeResponder({"空心菜": "E5600101"}))
        with session_factory()() as db:
            after = {(g.kind, g.food_id): g for g in batch.scan(db)}
        gap = after[("cooking", "FOOD000509")]
        self.assertEqual(gap.input_snapshot["tfda_raw"]["sample_id"], "E5600101")


@requires_db
class MappingCommandLineTests(unittest.TestCase):

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

    def test_override_approve_reject_and_list(self):
        code, _, err = self.cli("override", "--kind", "tfda_mapping", "--food", "FOOD000691", "--key", "tfda",
                                "--sample", "P0300101", "--reviewer", "營養師A", "--note", "岩鹽近似")
        self.assertEqual(code, 0, err)
        with session_factory()() as db:
            res = tfda_mapping.load_resolver(db).resolve("FOOD000691", "食鹽")
        self.assertEqual((res.source, res.sample_id), ("human", "P0300101"))
        code, out, _ = self.cli("list", "--kind", "tfda_mapping")
        [row] = json.loads(out)
        self.assertEqual(row["tfda_sample_id"], "P0300101")
        self.assertEqual(self.cli("reject", row["id"], "--reviewer", "營養師B")[0], 0)
        with session_factory()() as db:
            self.assertIsNone(tfda_mapping.load_resolver(db).resolve("FOOD000691", "食鹽"))

    def test_override_with_unknown_sample_is_refused(self):
        code, _, err = self.cli("override", "--kind", "tfda_mapping", "--food", "FOOD000691", "--key", "tfda",
                                "--sample", "NOPE", "--reviewer", "營養師A")
        self.assertEqual(code, 1)
        self.assertIn("NOPE", err)


if __name__ == "__main__":
    unittest.main()
