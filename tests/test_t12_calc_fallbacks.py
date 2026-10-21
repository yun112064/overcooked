"""T12 計算核心後備規則（全面診斷 wf_a3cff80a-cc0 後的決策 #49、#51）。

以真實 T04 nutrition_model（TFDA 資料）計算，不呼叫 LLM：
- #49(a) TFDA 狀態未標生熟（unspecified）的調味料、糖、油脂、香料、乾貨等：有烹調方式時仍以原樣組成計算；
- #49(b) 其他生樣本查不到烹調係數：TFDA 生組成 × 生重近似（熱量、蛋白質也適用），trace 標 raw_composition_fallback；
- #49(c) 方式正規化：與快取組合鍵相同規則的公開函式；
- #51 市售即為熟品的食材對照到 TFDA 熟樣本時以熟重計算（工作簿慣例與 AI 宣告兩種來源）。
"""
import unittest
from decimal import Decimal

from app.meal_calc.types import EstimatedFactor
from app.services import meal_calculation as mc
from app.services.tfda_mapping import TfdaResolver

D = Decimal
MODEL = "gpt-5.4-mini-2026-03-17"


def line(name, grams, food_id=None, **kw):
    return mc.IngredientLine(food_id=food_id or f"F-{name}", name=name, grams_per_n=D(grams), **kw)


def mapping(food_id, sample_id, status="overridden", source="human"):
    return EstimatedFactor(estimate_id=f"map-{food_id}", kind="tfda_mapping", food_id=food_id, key="tfda",
                           source=source, status=status, model=None, prompt_version=None, schema_version=None,
                           tfda_sample_id=sample_id)


def cooking(food_id, method, *, yield_factor="0.7", energy="0.95", protein="0.9"):
    return EstimatedFactor(
        estimate_id="est-c", kind="cooking", food_id=food_id, key=method, source="llm_estimated",
        status="llm_estimated", model=MODEL, prompt_version="p1", schema_version="s1",
        yield_factor=D(yield_factor), retention={"energy_kcal": D(energy), "protein_g": D(protein)})


def lookup(*factors):
    table = {(f.food_id, f.key): f for f in factors}
    return lambda food_id, method: table.get((food_id, method))


def calc(*lines, factors=None, mappings=None):
    calculator = mc.make_tfda_calculator(cooking_factors=factors,
                                         tfda_resolver=TfdaResolver(mappings=mappings or {}))
    return calculator([(ln, ln.grams_per_n) for ln in lines])


class UnspecifiedAsIsTests(unittest.TestCase):
    """#49(a)：AI 合法食材 428 項中有 252 項是 unspecified（食鹽、醬油、米酒、油、八角…）。"""

    def test_seasoning_with_a_cooking_method_uses_its_composition_as_is(self):
        # 醬油 P0700101：每 100 g 熱量 TFDA 值；有烹調方式也不套烹調、不報 ingredient_not_found
        [soy] = calc(line("醬油", "15", cooking_method="炒"))
        self.assertIsNone(soy.error)
        self.assertEqual(soy.trace["sample_id"], "P0700101")
        self.assertIsNotNone(soy.nutrients["energy_kcal"])
        self.assertIsNotNone(soy.nutrients["protein_g"])
        self.assertEqual(soy.trace["nutrition_basis"], mc.UNSPECIFIED_AS_IS)
        self.assertEqual(soy.trace["as_is_rule"], "seasonings_spices")

    def test_multi_method_recipe_seasoning_needs_no_combined_key_factor(self):
        # 資料庫食譜多種方式（cooking_method 不填、只留組合鍵）：調味料仍以原樣組成計算
        [soy] = calc(line("醬油", "15", cooking_methods=("炒", "煮")))
        [plain] = calc(line("醬油", "15", consumed_as_is=True))
        self.assertEqual(soy.nutrients, plain.nutrients)

    def test_dried_goods_and_wine_are_as_is_even_when_a_cached_factor_exists(self):
        # 乾木耳（乾貨）、紹興酒：診斷中推估係數 OUT_OF_RANGE 的組合，照原樣組成計算
        factors = lookup(cooking("F-乾木耳平均值", "stir", yield_factor="8"))
        [fungus] = calc(line("乾木耳平均值", "5", cooking_method="炒"), factors=factors)
        [plain] = calc(line("乾木耳平均值", "5", consumed_as_is=True))
        self.assertEqual(fungus.nutrients, plain.nutrients)
        self.assertEqual(fungus.trace["estimated_factors"], [])
        [wine] = calc(line("陳年紹興酒", "15", cooking_methods=("boil", "steam")))
        self.assertIsNotNone(wine.nutrients["energy_kcal"])
        self.assertEqual(wine.trace["nutrition_basis"], mc.UNSPECIFIED_AS_IS)

    def test_rules_live_in_a_config_with_reasons(self):
        rules = mc.as_is_rules()
        self.assertTrue(rules)
        self.assertTrue(all(r["reason"] and r["prefixes"] for r in rules))


class RawCompositionFallbackTests(unittest.TestCase):
    """#49(b)：生樣本查不到烹調係數時用 TFDA 生組成 × 生重近似（熱量、蛋白質也適用）。"""

    def test_single_method_without_factor_uses_raw_composition(self):
        # 雞胸肉 I0402402 生樣本；AI 單一方式「烤」沒有快取係數
        [chicken] = calc(line("雞胸肉", "150", cooking_method="烤"))
        [raw] = calc(line("雞胸肉", "150", consumed_as_is=True))
        self.assertIsNone(chicken.error)
        self.assertEqual(chicken.nutrients["energy_kcal"], raw.nutrients["energy_kcal"])
        self.assertEqual(chicken.nutrients["protein_g"], raw.nutrients["protein_g"])
        self.assertTrue(chicken.trace["raw_composition_fallback"])
        self.assertEqual(chicken.trace["nutrition_basis"], mc.RAW_COMPOSITION_FALLBACK)
        self.assertIn("energy_kcal", chicken.trace["approximate_nutrients"])

    def test_multi_method_key_without_factor_uses_raw_composition(self):
        [chicken] = calc(line("雞胸肉", "150", cooking_methods=("炒", "拌")))
        self.assertTrue(chicken.trace["raw_composition_fallback"])
        self.assertIsNotNone(chicken.nutrients["protein_g"])

    def test_cached_factor_still_wins_over_the_fallback(self):
        factors = lookup(cooking("F-雞胸肉", "roast"))
        [chicken] = calc(line("雞胸肉", "150", cooking_method="烤"), factors=factors)
        [raw] = calc(line("雞胸肉", "150", consumed_as_is=True))
        self.assertEqual(chicken.nutrients["energy_kcal"], raw.nutrients["energy_kcal"] * D("0.95"))
        self.assertFalse(chicken.trace.get("raw_composition_fallback"))

    def test_white_rice_mapped_to_raw_japonica_rice_is_computable(self):
        # 白米 FOOD000280 由協調者 override 對到 A05002 稉米平均值（生）：食譜「煮」或多方式都算得出
        maps = {"FOOD000280": mapping("FOOD000280", "A05002")}
        [boiled] = calc(line("白米", "80", food_id="FOOD000280", cooking_method="煮",
                             weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), mappings=maps)
        self.assertEqual(boiled.trace["sample_id"], "A05002")
        self.assertEqual(boiled.nutrients["energy_kcal"], D("283.2"))  # 354 kcal/100 g × 80 g
        self.assertEqual(boiled.nutrients["protein_g"], D("5.6"))
        [mixed] = calc(line("白米", "80", food_id="FOOD000280", cooking_methods=("煮", "炒"),
                            weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), mappings=maps)
        self.assertEqual(mixed.nutrients["energy_kcal"], D("283.2"))

    def test_bacon_fiber_without_fill_stays_unknown_not_zero(self):
        # 培根 R5100801 TFDA 膳食纖維空白、沒有推估：仍是 None（不以 0 代替），其餘照算
        [bacon] = calc(line("培根", "30", cooking_method="煎"))
        self.assertIsNotNone(bacon.nutrients["energy_kcal"])
        self.assertIsNone(bacon.nutrients["dietary_fiber_g"])


class MarketCookedTests(unittest.TestCase):
    """#51：市售即為熟品（桂竹筍、鹹蛋黃、杏仁果）對照到 TFDA 熟樣本 → 克數視為熟重、不套烹調。"""

    def test_bamboo_shoot_from_workbook_is_cooked_weight(self):
        maps = {"FOOD000728": mapping("FOOD000728", "E1100101", status="llm_estimated", source="llm_estimated")}
        [shoot] = calc(line("桂竹筍", "200", food_id="FOOD000728", cooking_method="炒",
                            weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), mappings=maps)
        self.assertIsNone(shoot.error)
        self.assertEqual(shoot.nutrients["energy_kcal"], D("42"))  # 熟桂竹筍 21 kcal/100 g
        self.assertEqual((shoot.trace["weight_basis"], shoot.trace["weight_basis_source"]),
                         ("cooked", mc.MARKET_COOKED_SAMPLE))

    def test_ai_declared_salted_egg_yolk_is_cooked_weight(self):
        maps = {"FOOD001642": mapping("FOOD001642", "K0214801")}
        [yolk] = calc(line("鹹蛋黃", "20", food_id="FOOD001642", cooking_method="炒",
                           weight_basis_source=mc.MODEL_DECLARED), mappings=maps)
        self.assertEqual(yolk.nutrients["energy_kcal"], D("105.6"))  # 528 × 0.2

    def test_ai_declared_cooked_food_name_extends_decision_35(self):
        # AI 菜的「白飯」宣告生重＋烹調方式：對到熟樣本 A0550601 → 熟重（#35 擴充到 MODEL_DECLARED）
        [rice] = calc(line("白飯", "150", cooking_method="炒", weight_basis_source=mc.MODEL_DECLARED))
        self.assertIsNone(rice.error)
        self.assertEqual(rice.nutrients["energy_kcal"], D("274.5"))

    def test_market_cooked_list_is_configured_with_reasons(self):
        names = {e["name"] for e in mc.market_cooked_entries()}
        self.assertTrue({"桂竹筍", "鹹蛋黃", "杏仁果"} <= names)
        self.assertTrue(all(e["reason"] for e in mc.market_cooked_entries()))

    def test_raw_food_mapped_to_a_cooked_sample_is_still_rejected(self):
        # 不在清單、名稱也不是熟食者維持 #17：生重配熟樣本仍報錯（白米錯對到白飯要靠 override 修正）
        maps = {"FOOD000280": mapping("FOOD000280", "A0550601", status="llm_estimated", source="llm_estimated")}
        [rice] = calc(line("白米", "80", food_id="FOOD000280", cooking_method="煮",
                           weight_basis_source=mc.WORKBOOK_RAW_CONVENTION), mappings=maps)
        self.assertEqual(rice.error, "cooked_food_requires_cooked_weight_basis")


class MethodKeyTests(unittest.TestCase):
    """#49(c)：AI 路徑與快取組合鍵共用同一套正規化。"""

    def test_single_and_combined_methods_match_the_cache_keys(self):
        self.assertEqual(mc.cooking_method_key("炒"), "stir")
        self.assertEqual(mc.cooking_method_key(["拌", "炒"]), mc.combined_method_key(["炒", "拌"]))
        self.assertEqual(mc.cooking_method_key("炒、拌"), mc.combined_method_key(["炒", "拌"]))
        self.assertEqual(mc.cooking_method_key("煮+炒"), "boil+stir")
        self.assertIsNone(mc.cooking_method_key(""))
        self.assertIsNone(mc.cooking_method_key(None))

    def test_common_synonyms_map_to_the_workbook_vocabulary(self):
        for text, expected in (("清炒", "stir"), ("油炸", "炸"), ("汆燙", "燙"), ("清蒸", "steam"), ("涼拌", "拌"),
                               ("紅燒", "braise"), ("香煎", "pan")):
            with self.subTest(text=text):
                self.assertEqual(mc.cooking_method_key(text), expected)


if __name__ == "__main__":
    unittest.main()
