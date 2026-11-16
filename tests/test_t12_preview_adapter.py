"""資料庫菜單的 T12 營養預覽（services/t12_planning.py，demo 版；不連資料庫）。

食譜載入、目標、限制與 calculator 以替身提供；calculate_meal 以假的計算結果（與真實 calculate_meal 同欄位）
或真實函式搭配每克固定含量的假 calculator。檢查的是投影到畫面契約（frontend-3d NutritionPreview）的規則：
未知不當 0、不可採用的狀態與說明、主食防線、每人判定與可採用判定同一條規則、_calc 不外流。
"""
import os
import unittest
import uuid
from datetime import date
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import patch

# 設定物件要求 DATABASE_URL；這些測試不連資料庫，只放占位值（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.meal_calc.types import IngredientRequirement
from app.services import seasonings, t12_planning
from app.services.meal_calculation import IngredientLine, IngredientNutrition, _enough
from app.services.meal_request import MealRequestDraft

RICE = "FOOD000296"      # config/staples.json 白飯
CABBAGE = "FOOD000867"
GARLIC = "FOOD000039"    # config/garnish_ingredients.json 大蒜
MEMBER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")


def draft(**extra):
    fields = {"meal_date": date(2026, 8, 20), "meal_type": "dinner", "budget": Decimal(300), "staple": None,
              "dish_count": 1, "participants": [], "specified_dishes": []}
    fields.update(extra)
    return MealRequestDraft(**fields)


def targets(*people):
    """people：(participation_id, 熱量, 蛋白質)；整餐目標為各份合計。"""
    people = people or ((f"{MEMBER}:dining", "600", "30"),)
    energy = sum(Decimal(e) for _, e, _ in people)
    protein = sum(Decimal(p) for _, _, p in people)
    return {"status": "available", "rule_version": "test", "total_n": str(len(people)),
            "target": {"energy_kcal": str(energy), "protein_g": str(protein)},
            "items": [{"participation_id": pid, "kind": "dining", "meal": "dinner", "meal_ratio": "0.4", "n": "1",
                       "scaled_target": {"energy_kcal": e, "protein_g": p}} for pid, e, p in people]}


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    """只回答 _summaries 的食譜查詢。"""

    def __init__(self, recipes):
        self.recipes = recipes

    def execute(self, _stmt):
        return FakeRows(self.recipes)

    def get_bind(self):
        return "fake-bind"


def recipe(recipe_id="R1", name="清炒高麗菜", eligible=True):
    return SimpleNamespace(recipe_id=recipe_id, recipe_name=name, name_aliases=[], category="主菜",
                           is_eligible=eligible)


def line(food_id, name, grams, dish="R1", **kw):
    return IngredientLine(food_id=food_id, name=name, grams_per_n=None if grams is None else Decimal(grams),
                          dish_id=dish, unit="g", **kw)


def dishes_for(lines_by_recipe):
    def _database_dishes(_db, summaries):
        dishes, lines = [], []
        for s in summaries:
            recipe_lines = lines_by_recipe[s["recipe_id"]]
            lines += recipe_lines
            dishes.append({"standard_dish_id": s["standard_dish_id"], "dish_name": s["recipe_name"],
                           "aliases": s["aliases"], "source": "database", "recipe_id": s["recipe_id"],
                           "specified": s["specified"], "steps": ["炒"], "steps_display": ["炒熟"],
                           "food_ids": sorted({ln.food_id for ln in recipe_lines})})
        return dishes, lines
    return _database_dishes


def calculator(per_gram: dict, unknown: frozenset = frozenset()):
    """每克固定含量；unknown 的食材熱量與蛋白質缺值。"""
    def calculate(rows):
        out = []
        for ln, grams in rows:
            if ln.food_id in unknown:
                out.append(IngredientNutrition({"energy_kcal": None, "protein_g": None}, {}, "no_sample"))
                continue
            e, p = per_gram.get(ln.food_id, (Decimal(1), Decimal("0.05")))
            out.append(IngredientNutrition({"energy_kcal": e * grams, "protein_g": p * grams}, {}, None))
        return out
    return calculate


def fake_calc(lines, *, energy, protein, adoptable, block_reasons=(), others=None, known=None, missing=None,
              n=Decimal(1)):
    """與 calculate_meal 同欄位的假結果；克數未知的列整餐用量為 None。"""
    rows = [{"food_id": ln.food_id, "name": ln.name, "dish_id": ln.dish_id, "role": ln.role,
             "whole_meal_grams": None if ln.grams_per_n is None else ln.grams_per_n * n,
             "display_amount": None if ln.grams_per_n is None else ln.grams_per_n * n,
             "display_unit": None if ln.grams_per_n is None else "g",
             "display_text": None if ln.grams_per_n is not None else ln.name} for ln in lines]
    return {"mode": "database", "adoptable": adoptable, "block_reasons": list(block_reasons),
            "quantities_verified": adoptable, "total_n": n,
            "baseline": {"adjusted": False}, "dish_scales": {ln.dish_id: Decimal(1) for ln in lines
                                                             if ln.role != "staple"},
            "final": {"target": {}, "energy_kcal": energy, "protein_g": protein, "other_nutrients": others or {},
                      "known_partial": known or {}, "missing_by_nutrient": missing or {}, "missing_nutrients": {}},
            "ingredients": rows,
            "requirements": [IngredientRequirement(ln.food_id, ln.grams_per_n * n) for ln in lines
                             if ln.role != "staple" and ln.grams_per_n is not None],
            "unresolved_ingredients": [{"food_id": ln.food_id, "name": ln.name, "reason": "weight_unresolved"}
                                       for ln in lines if ln.grams_per_n is None]}


OTHERS = {"carbohydrate_g": Decimal(80), "fat_g": Decimal(20), "sugars_g": Decimal(5), "sodium_mg": Decimal(500),
          "dietary_fiber_g": Decimal(6)}


class PreviewCase(unittest.TestCase):
    def run_preview(self, lines_by_recipe, *, recipes=None, calc=None, excluded=(), staple=None,
                    target_info=None, per_gram=None, unknown=frozenset(), members=None, recipe_ids=None):
        recipes = recipes if recipes is not None else [recipe(rid) for rid in lines_by_recipe]
        patches = [
            patch.object(t12_planning.meal_request, "target_preview", return_value=target_info or targets()),
            patch.object(t12_planning.meal_request, "restrictions",
                         return_value={"excluded_food_ids": sorted(excluded)}),
            patch.object(t12_planning, "_calculator", return_value=calculator(per_gram or {}, unknown)),
            patch.object(t12_planning.generation, "_database_dishes", side_effect=dishes_for(lines_by_recipe)),
        ]
        if calc is not None:
            patches.append(patch.object(t12_planning, "calculate_meal", side_effect=calc))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        members = members if members is not None else {MEMBER: SimpleNamespace(display_name="成員A")}
        return t12_planning.preview(FakeDb(recipes), draft=draft(staple=staple), members=members,
                                    recipe_ids=recipe_ids or list(lines_by_recipe))


class UnknownStaysUnknownTests(PreviewCase):
    def test_unknown_nutrient_is_none_and_listed_in_known_partial(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        others = {**OTHERS, "sodium_mg": None}
        others.pop("dietary_fiber_g")  # calculator 完全沒有回傳的營養也是未知
        result = self.run_preview(lines, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(590), protein=Decimal(31), adoptable=True, others=others,
            known={"sodium_mg": Decimal(300)}, missing={"sodium_mg": ["高麗菜"]}))
        totals = result["nutrition_all"]["totals"]
        self.assertIsNone(totals["sodium_mg"])
        self.assertIsNone(totals["dietary_fiber_g"])
        self.assertEqual(result["nutrition"]["totals"]["energy_kcal"], "590")
        self.assertEqual(result["nutrition_all"]["known_partial"], {"sodium_mg": "300", "dietary_fiber_g": None})
        self.assertEqual(result["nutrition_all"]["missing_by_nutrient"]["sodium_mg"], ["高麗菜"])
        self.assertEqual(sorted(result["nutrition"]["missing"]), ["dietary_fiber_g", "sodium_mg"])
        [person] = result["per_person"]
        self.assertIsNone(person["nutrition_all"]["sodium_mg"])
        self.assertEqual(Decimal(person["nutrition_known_partial"]["sodium_mg"]), Decimal(300))
        self.assertNotIn("dietary_fiber_g", person["nutrition_known_partial"])

    def test_unresolved_ingredient_is_incomplete_and_blocked_with_null_grams(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300"), line(GARLIC, "大蒜", None)]}
        reasons = [{"code": "INGREDIENT_UNRESOLVED", "food_ids": [GARLIC]},
                   {"code": "NUTRITION_INCOMPLETE", "nutrients": ["energy_kcal", "protein_g"]}]
        result = self.run_preview(lines, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=None, protein=None, adoptable=False, block_reasons=reasons,
            others={k: None for k in OTHERS}, known={"energy_kcal": Decimal(300), "protein_g": Decimal(15)}))
        self.assertEqual(result["status"], "calculation_incomplete")
        self.assertFalse(result["nutrition_passed"])
        self.assertTrue(result["adoption_blocked"])
        self.assertIn("大蒜", result["status_message"])
        garlic = next(i for i in result["dishes"][0]["ingredients"] if i["food_id"] == GARLIC)
        self.assertIsNone(garlic["grams"])
        self.assertIsNone(result["nutrition"]["totals"]["energy_kcal"])
        self.assertEqual(result["nutrition_all"]["known_partial"]["energy_kcal"], "300")
        self.assertEqual(result["final_food_ids"], [])
        self.assertIn("INGREDIENT_UNRESOLVED", [i["code"] for i in result["input_issues"]])
        [person] = result["per_person"]
        self.assertEqual(person["within_10pct"], {"energy_kcal": False, "protein_g": False})  # 未知不算通過


class StatusTests(PreviewCase):
    def test_adoptable_menu_passes_with_empty_message(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300"), line(GARLIC, "大蒜", "5")]}
        result = self.run_preview(lines, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(590), protein=Decimal(31), adoptable=True, others=OTHERS))
        self.assertEqual(result["status"], "nutrition_passed")
        self.assertEqual(result["status_message"], "")
        self.assertTrue(result["nutrition_passed"])
        self.assertFalse(result["adoption_blocked"])
        self.assertEqual(result["targets"], {"energy_kcal": "600", "protein_g": "30"})
        self.assertEqual(result["dishes"][0]["steps"], ["炒熟"])
        self.assertEqual(result["dishes"][0]["ingredients"][0]["grams"], "300")
        self.assertEqual(result["input_issues"], [])

    def test_outside_tolerance_has_a_message(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        reasons = [{"code": "NUTRITION_OUT_OF_RANGE", "stage": "baseline", "nutrient": "energy_kcal"},
                   {"code": "NO_FEASIBLE_BASELINE"}]
        result = self.run_preview(lines, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(900), protein=Decimal(31), adoptable=False, block_reasons=reasons, others=OTHERS))
        self.assertEqual(result["status"], "outside_tolerance")
        self.assertTrue(result["adoption_blocked"])
        self.assertIn("熱量超過目標 110%", result["status_message"])
        self.assertEqual(result["final_food_ids"], [])

    def test_unknown_or_ineligible_recipe_is_menu_changed(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        with self.assertRaises(t12_planning.T12MenuChanged) as caught:
            self.run_preview(lines, recipes=[], recipe_ids=["R1"])
        self.assertEqual((caught.exception.code, caught.exception.status), ("MENU_CHANGED", 409))
        with self.assertRaises(t12_planning.T12MenuChanged) as caught:
            self.run_preview(lines, recipes=[recipe("R1", eligible=False)])
        self.assertEqual((caught.exception.code, caught.exception.status), ("MENU_CHANGED", 409))
        self.assertEqual(t12_planning.T12PlanningUnavailable("RECIPE_DATA_UNAVAILABLE", "x").status, 503)

    def test_final_food_ids_exclude_the_staple(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300"), line(GARLIC, "大蒜", "5")]}
        result = self.run_preview(lines, staple="白飯", calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(590), protein=Decimal(31), adoptable=True, others=OTHERS))
        self.assertEqual(result["status"], "nutrition_passed")
        self.assertEqual(result["final_food_ids"], sorted([CABBAGE, GARLIC]))
        self.assertEqual(result["staple"], {"name": "白飯", "food_id": RICE, "final_grams": "150"})
        self.assertEqual(result["per_person"][0]["staple_g"], "150")
        self.assertNotIn(RICE, [i["food_id"] for d in result["dishes"] for i in d["ingredients"]])


class StapleGuardTests(PreviewCase):
    def test_staple_with_excluded_food_is_blocked_without_raising(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        result = self.run_preview(lines, staple="白飯", excluded={RICE}, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(590), protein=Decimal(31), adoptable=True, others=OTHERS))
        self.assertEqual(result["status"], "calculation_incomplete")
        self.assertFalse(result["nutrition_passed"])
        self.assertTrue(result["adoption_blocked"])
        self.assertEqual([i["code"] for i in result["input_issues"]], ["STAPLE_DIETARY_CONFLICT"])
        self.assertIn("白飯", result["status_message"])
        self.assertEqual(result["final_food_ids"], [])

    def test_staple_without_known_energy_or_protein_is_blocked(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        result = self.run_preview(lines, staple="白飯", unknown=frozenset({RICE}),
                                  calc=lambda ls, *a, **k: fake_calc(
                                      ls, energy=None, protein=None, adoptable=False, others=OTHERS,
                                      block_reasons=[{"code": "NUTRITION_INCOMPLETE",
                                                      "ingredients": {"energy_kcal": ["白飯"]}}]))
        self.assertEqual(result["status"], "calculation_incomplete")
        self.assertTrue(result["adoption_blocked"])
        self.assertEqual([i["code"] for i in result["input_issues"]], ["STAPLE_NUTRITION_UNAVAILABLE"])
        self.assertIn("白飯", result["status_message"])

    def test_unsupported_staple_text_is_blocked(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        result = self.run_preview(lines, staple="披薩餅皮", calc=lambda ls, *a, **k: fake_calc(
            ls, energy=None, protein=None, adoptable=False, others=OTHERS,
            block_reasons=[{"code": "INGREDIENT_UNRESOLVED"}, {"code": "NUTRITION_INCOMPLETE"}]))
        self.assertEqual(result["input_issues"][0]["code"], "STAPLE_NUTRITION_UNAVAILABLE")
        self.assertTrue(result["adoption_blocked"])

    def test_dish_with_excluded_food_is_blocked(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300"), line(GARLIC, "大蒜", "5")]}
        result = self.run_preview(lines, excluded={GARLIC}, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(590), protein=Decimal(31), adoptable=True, others=OTHERS))
        self.assertTrue(result["adoption_blocked"])
        self.assertIn("DIETARY_CONFLICT", [i["code"] for i in result["input_issues"]])
        self.assertTrue(result["status_message"])


class RuleAgreementTests(PreviewCase):
    """每人判定與可採用判定（meal_calculation._enough）同一條規則：熱量 ≤ 110%、蛋白質 ≥ 90%。"""

    def per_person(self, energy, protein, **kw):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        reasons = [] if kw.get("adoptable", True) else [{"code": "NUTRITION_OUT_OF_RANGE", "stage": "final",
                                                         "nutrient": kw["nutrient"]}]
        result = self.run_preview(lines, calc=lambda ls, *a, **k: fake_calc(
            ls, energy=Decimal(energy), protein=Decimal(protein), adoptable=kw.get("adoptable", True),
            block_reasons=reasons, others=OTHERS))
        return result, result["per_person"][0]["within_10pct"]

    def enough(self, energy, protein):
        refs = {"energy_kcal": Fraction(600), "protein_g": Fraction(30)}
        return {"energy_kcal": _enough(Fraction(energy), "energy_kcal", refs),
                "protein_g": _enough(Fraction(protein), "protein_g", refs)}

    def test_85_percent_energy_and_120_percent_protein_pass(self):
        result, within = self.per_person("510", "36")
        self.assertEqual(within, {"energy_kcal": True, "protein_g": True})
        self.assertEqual(within, self.enough("510", "36"))
        self.assertEqual(result["status"], "nutrition_passed")

    def test_111_percent_energy_fails(self):
        result, within = self.per_person("666", "30", adoptable=False, nutrient="energy_kcal")
        self.assertFalse(within["energy_kcal"])
        self.assertEqual(within, self.enough("666", "30"))
        self.assertEqual(result["status"], "outside_tolerance")

    def test_89_percent_protein_fails(self):
        result, within = self.per_person("600", "26.7", adoptable=False, nutrient="protein_g")
        self.assertFalse(within["protein_g"])
        self.assertEqual(within, self.enough("600", "26.7"))
        self.assertIn("蛋白質低於目標 90%", result["status_message"])

    def test_shares_follow_energy_targets_and_dish_shares_sum_to_one(self):
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        info = targets((f"{MEMBER}:dining", "400", "20"), (f"{OTHER}:dining", "200", "10"))
        result = self.run_preview(lines, target_info=info, members={
            MEMBER: SimpleNamespace(display_name="成員A"), OTHER: SimpleNamespace(display_name="成員B")},
            calc=lambda ls, *a, **k: fake_calc(ls, energy=Decimal(600), protein=Decimal(30), adoptable=True,
                                                 others=OTHERS, n=Decimal(2)))
        a, b = result["per_person"]
        self.assertEqual(sum(Decimal(p["dish_share"]) for p in (a, b)), Decimal(1))
        self.assertEqual(Decimal(a["nutrition"]["energy_kcal"]), Decimal(400))
        self.assertEqual(a["within_10pct"], {"energy_kcal": True, "protein_g": True})
        self.assertEqual([p["name"] for p in result["people"]], ["成員A", "成員B"])
        self.assertEqual(result["people"][0]["dining"]["intake"]["energy_kcal"], a["nutrition"]["energy_kcal"])
        self.assertIsNone(result["people"][0]["bento"])


class RealCalculationTests(PreviewCase):
    """真實 calculate_meal（不替換）＋每克固定含量的 calculator：預覽與生成工作走同一條計算。"""

    def test_real_calculation_passes_and_costs_requirements(self):
        # 每份目標 600 kcal／30 g：高麗菜 300 g × 2 kcal = 600 kcal、× 0.1 g = 30 g
        lines = {"R1": [line(CABBAGE, "高麗菜", "300")]}
        result = self.run_preview(lines, per_gram={CABBAGE: (Decimal(2), Decimal("0.1"))})
        self.assertEqual(result["status"], "nutrition_passed", result["status_message"])
        self.assertEqual(Decimal(result["nutrition"]["totals"]["energy_kcal"]), Decimal(600))
        self.assertEqual(result["_calc"]["requirements"], [IngredientRequirement(CABBAGE, Decimal(300))])
        self.assertEqual(result["final_food_ids"], [CABBAGE])


class DisplayTests(unittest.TestCase):
    def test_group_helper_reads_the_two_config_files(self):
        seasoning = sorted(seasonings.load_seasonings())[0]
        self.assertEqual(t12_planning.display_group(seasoning), "調味")
        self.assertEqual(t12_planning.display_group(GARLIC), "佐料")
        self.assertEqual(t12_planning.display_group(CABBAGE), "主食材")
        self.assertEqual(t12_planning.display_group(None), "主食材")

    def test_seasoning_uses_spoon_display_and_others_keep_calc_display(self):
        salt = "FOOD000691"
        row = {"whole_meal_grams": Decimal("2.5"), "display_amount": Decimal(3), "display_unit": "g",
               "display_text": None}
        shown = t12_planning._with_spoons({"food_id": salt, "name": "食鹽", "unit": "g", "grams": "2.5"}, row)
        self.assertEqual(shown["group"], "調味")
        self.assertIn(shown["display_unit"], ("小匙", None))
        cabbage = t12_planning._with_spoons({"food_id": CABBAGE, "name": "高麗菜", "unit": "g", "grams": "300"},
                                            {**row, "whole_meal_grams": Decimal(300), "display_amount": Decimal(300)})
        self.assertEqual((cabbage["display_amount"], cabbage["display_unit"]), ("300", "g"))


class RouteTests(unittest.TestCase):
    def test_route_never_returns_the_private_calculation(self):
        from app.api import meal_requests as api

        row = SimpleNamespace(household_id="h", payload={"meal_date": "2026-08-20", "meal_type": "dinner"})
        preview = {"status": "nutrition_passed", "status_message": "", "_calc": {"requirements": []}}
        with patch.object(api, "get_owned_request", return_value=row), \
                patch.object(api, "_reject_bad_references"), patch.object(api, "_members", return_value={}), \
                patch.object(api.service, "completeness", return_value=[]), \
                patch.object(api.t12_planning, "preview", return_value=preview):
            out = api.nutrition_preview(uuid.uuid4(), api.NutritionPreviewBody(recipe_ids=["R1"]),
                                        household_id="h", db=object())
        self.assertNotIn("_calc", out)
        self.assertEqual(out["status"], "nutrition_passed")
        self.assertIn("_calc", preview)  # 只移除回應，不改採用流程用的原始結果

    def test_unavailable_maps_to_503(self):
        from app.api import meal_requests as api
        from app.api.errors import ApiError

        row = SimpleNamespace(household_id="h", payload={"meal_date": "2026-08-20", "meal_type": "dinner"})
        with patch.object(api, "get_owned_request", return_value=row), \
                patch.object(api, "_reject_bad_references"), patch.object(api, "_members", return_value={}), \
                patch.object(api.service, "completeness", return_value=[]), \
                patch.object(api.t12_planning, "preview",
                             side_effect=t12_planning.T12PlanningUnavailable("RECIPE_DATA_UNAVAILABLE", "x")):
            with self.assertRaises(ApiError) as caught:
                api.nutrition_preview(uuid.uuid4(), api.NutritionPreviewBody(recipe_ids=["R1"]),
                                      household_id="h", db=object())
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
