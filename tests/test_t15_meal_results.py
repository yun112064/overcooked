"""T15：生成結果讀取、AI 標示、知識說明與缺值決策（R02、R05–R09、R12、R13；AC07–AC09、AC12、AC15–AC18、AC29、AC31、AC32）。

沿用 T14 測試的整條流程（問卷 → T10 工作 → generation_results），付費模型／知識服務以假物件取代。
價格以 T02 PricingRepository 寫入合成快照。合成食譜每道兩項食材、每項 50 g/n（一位成員 N=1），
因此整餐每項需求 50 g；報價 300 g／30 元 → 採購價 30 元、比例成本 5 元
（T13 以 Decimal 精確計算 50/300×30，末位有 1e-27 誤差，比較時取到 0.01 元）。
"""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import db_support
from test_t13_meal_cost import quote
from test_t14_generation import (CABBAGE, EGG, GARLIC, TOMATO, FakeKnowledgeClient, FakeResponder,
                                 GenerationTestCase, ai_dish, menu, per_gram_calculator)

ALL = (TOMATO, EGG, CABBAGE, GARLIC)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))
_runs = iter(range(1, 10_000))


def seed_prices(*food_ids, price="30", qty="300"):
    from app.source_adapters.pricing_repository import PricingRepository
    now = datetime.now(timezone.utc)
    repo = PricingRepository(db_support._engine)
    lease = repo.try_begin_run(run_key=f"t15-{next(_runs)}", provider="pxmart", trigger_type="manual",
                               scheduled_for=now, started_at=now)
    for food_id in food_ids:
        repo.save_quote(lease, quote(food_id, price, qty, queried_at=now - timedelta(hours=1)))
    repo.finish_run(lease, finished_at=now, status="completed", success_count=0, review_count=0, failure_count=0,
                    duration_ms=1, status_counts={}, missing_reasons={})


class ResultTestCase(GenerationTestCase):
    def generate(self, *, prices=ALL, responder=None, calculator=None, knowledge=None, **fields):
        """calculator 可為函式工廠（無參數），在建立問卷後才呼叫，讓它能讀本餐目標。"""
        if prices:
            seed_prices(*prices)
        self.create_request(**{"dish_count": 2, **fields})
        if calculator is not None and getattr(calculator, "factory", False):
            calculator = calculator()
        job_id = self.submit()
        from knowledge_support import FakeEmbedder
        self.run_job(responder, knowledge=knowledge, embedder=FakeEmbedder() if knowledge else None,
                     calculator=calculator)
        return self.job(job_id)

    def result(self, version=1):
        res = self.client.get(f"/meal-requests/{self.request['id']}/results/{version}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def status(self):
        return self.client.get(f"/meal-requests/{self.request['id']}").json()["status"]

    def decide(self, kind, *, version=1, key="decision-0001", request_version=None):
        return self.client.post(
            f"/meal-requests/{self.request['id']}/results/{version}/decisions",
            json={"kind": kind, "request_version": request_version or self.request["version"]},
            headers={"Idempotency-Key": key})

    def retry_prices(self, *, version=1, key="retry-00000001"):
        return self.client.post(
            f"/meal-requests/{self.request['id']}/results/{version}/price-retry",
            json={"request_version": self.request["version"]}, headers={"Idempotency-Key": key})


class ResultViewTests(ResultTestCase):
    def test_complete_result_shows_family_recipe_dual_prices_nutrition_and_is_adoptable(self):
        job = self.generate()
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertEqual((view["result_version"], view["request_version"], view["mode"], view["mode_label"]),
                         (1, 1, "database_menu", "資料庫菜單"))
        # 全家配方：整餐克數（N=1 → 50 g）與步驟
        self.assertEqual([d["dish_name"] for d in view["dishes"]], ["番茄炒蛋", "清炒高麗菜"])
        first = view["dishes"][0]
        self.assertEqual([(i["food_id"], Decimal(i["whole_meal_grams"])) for i in first["ingredients"]],
                         [(TOMATO, Decimal(50)), (EGG, Decimal(50))])
        self.assertIsInstance(first["steps"], list)
        # 缺料雙價格與合計
        prices = view["prices"]
        self.assertEqual(sorted(i["food_id"] for i in prices["items"]), sorted(ALL))
        for item in prices["items"]:
            self.assertEqual((money(item["purchase_price"]), money(item["cost"]), item["packs"]),
                             (Decimal(30), Decimal(5), 1))
            self.assertEqual(item["source"], "PX Mart Hour Arrive public search")
            self.assertTrue(item["queried_at"])
            self.assertTrue(item["package_description"])
        self.assertEqual((money(prices["purchase_total"]), money(prices["cost_total"])),
                         (Decimal(120), Decimal(20)))
        self.assertEqual(prices["budget_status"], "OK")
        # 數值營養：目標、估算與差距
        energy, _ = self.reference()
        nutrition = view["nutrition"]
        self.assertEqual(Decimal(nutrition["energy_kcal"]), energy)
        self.assertEqual(Decimal(nutrition["gap"]["energy_kcal"]), Decimal(0))
        self.assertTrue(nutrition["within_range"])
        # 採用資格由後端判定
        self.assertTrue(view["eligibility"]["adoptable"], view["eligibility"])
        self.assertEqual(view["eligibility"]["pending_decisions"], [])

    def test_owned_ingredients_are_not_priced_but_still_count_for_nutrition(self):
        self.generate(prices=(), available_ingredient_ids=list(ALL))
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertEqual(view["prices"]["items"], [])
        self.assertEqual((Decimal(view["prices"]["purchase_total"]), Decimal(view["prices"]["cost_total"])),
                         (Decimal(0), Decimal(0)))
        energy, _ = self.reference()
        self.assertEqual(Decimal(view["nutrition"]["energy_kcal"]), energy)
        self.assertTrue(view["eligibility"]["adoptable"])

    def test_results_are_household_scoped(self):
        self.generate()
        other = db_support.make_client()
        other.post("/auth/register", json={"email": "other@example.test", "password": "correct-horse-42"})
        other.post("/household", json={"name": "別家"})
        self.assertEqual(other.get(f"/meal-requests/{self.request['id']}/results/1").status_code, 404)
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}/results/9").status_code, 404)

    def test_result_list_and_failure_keeps_questionnaire(self):
        self.clear_recipes()  # 需要 AI，但未設定 AI 服務 → 明確失敗
        job = self.generate(taste_preferences="清淡")
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "GENERATION_FAILED"))
        self.assertEqual(self.status(), "failed")
        request = self.client.get(f"/meal-requests/{self.request['id']}").json()
        self.assertEqual(request["draft"]["taste_preferences"], "清淡")
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}/results").json()["results"], [])


class AiAndPreviousDayTests(ResultTestCase):
    def test_ai_result_is_labelled_with_readable_fallback_reason(self):
        self.clear_recipes()
        responder = FakeResponder(menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], method="煮"),
                                       ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)])))
        self.generate(responder=responder)
        view = self.result()
        self.assertEqual((view["mode"], view["mode_label"], view["fallback_reason"]),
                         ("ai_generated_menu", "AI 生成菜單", "NO_MATCHING_RECIPES"))
        self.assertTrue(view["fallback_message"])
        self.assertEqual([d["source"] for d in view["dishes"]], ["ai", "ai"])
        self.assertEqual([(i["food_id"], Decimal(i["whole_meal_grams"])) for i in view["dishes"][1]["ingredients"]],
                         [(CABBAGE, Decimal(50)), (GARLIC, Decimal(50))])
        self.assertTrue(view["eligibility"]["adoptable"])

    def test_previous_day_comparison_and_specified_repeat_are_explained(self):
        self.adopt_yesterday("番茄炒蛋", "清炒高麗菜")
        self.generate(prices=(TOMATO, EGG, GARLIC, "FOOD000585"), specified_dishes=["番茄炒蛋"])
        view = self.result()
        previous = view["previous_day"]
        self.assertEqual((previous["previous_date"], previous["has_records"]), ("2026-08-19", True))
        self.assertEqual([d["dish_name"] for d in previous["excluded_dishes"]], ["清炒高麗菜"])
        self.assertEqual(previous["specified_repeats"], ["番茄炒蛋"])
        names = [d["dish_name"] for d in view["dishes"]]
        self.assertIn("番茄炒蛋", names)
        self.assertNotIn("清炒高麗菜", names)

    def test_no_previous_day_record_is_stated(self):
        self.generate()
        previous = self.result()["previous_day"]
        self.assertEqual((previous["has_records"], previous["excluded_dishes"], previous["specified_repeats"]),
                         (False, [], []))


class KnowledgeTests(ResultTestCase):
    def test_knowledge_service_failure_is_shown_separately_and_does_not_block(self):
        self.generate(knowledge=FakeKnowledgeClient(error="UNREACHABLE"), responder=FakeResponder())
        view = self.result()
        self.assertEqual((view["knowledge"]["status"], view["knowledge"]["code"]),
                         ("unavailable", "KNOWLEDGE_UNAVAILABLE"))
        self.assertTrue(view["eligibility"]["adoptable"])

    def test_missing_knowledge_is_omitted(self):
        self.generate(knowledge=FakeKnowledgeClient([]), responder=FakeResponder())
        view = self.result()
        self.assertEqual((view["knowledge"]["status"], view["knowledge"]["items"]), ("not_found", []))
        self.assertTrue(view["eligibility"]["adoptable"])


class MissingPriceDecisionTests(ResultTestCase):
    def test_missing_price_awaits_decision_then_explicit_acceptance_makes_it_adoptable(self):
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        self.assertEqual(self.status(), "awaiting_decision")
        view = self.result()
        prices = view["prices"]
        garlic = next(i for i in prices["items"] if i["food_id"] == GARLIC)
        self.assertEqual((garlic["price_status"], garlic["purchase_price"], garlic["cost"]), ("unknown", None, None))
        self.assertTrue(garlic["missing_reason"])
        self.assertEqual((prices["purchase_total"], prices["cost_total"]), (None, None))
        self.assertEqual((money(prices["known_purchase_total"]), money(prices["known_cost_total"])),
                         (Decimal(90), Decimal(15)))
        eligibility = view["eligibility"]
        self.assertFalse(eligibility["adoptable"])
        [pending] = eligibility["pending_decisions"]
        self.assertEqual((pending["kind"], pending["missing_food_ids"]), ("accept_missing_price", [GARLIC]))

        res = self.decide("accept_missing_price")
        self.assertEqual(res.status_code, 201, res.text)
        decision = res.json()["decision"]
        self.assertEqual((decision["kind"], decision["result_version"], decision["missing_food_ids"]),
                         ("accept_missing_price", 1, [GARLIC]))
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertTrue(view["eligibility"]["adoptable"])
        self.assertTrue(view["eligibility"]["price_exception_accepted"])
        self.assertEqual([d["kind"] for d in view["eligibility"]["decisions"]], ["accept_missing_price"])

        # 冪等：同鍵同命令回同一筆；同鍵不同命令拒絕
        again = self.decide("accept_missing_price")
        self.assertEqual((again.status_code, again.json()["decision"]["id"]), (200, decision["id"]))
        clash = self.decide("continue_missing_nutrition")
        self.assertEqual((clash.status_code, clash.json()["error"]["code"]), (409, "IDEMPOTENCY_CONFLICT"))

    def test_known_cost_over_budget_cannot_be_accepted(self):
        self.generate(prices=(TOMATO, EGG, CABBAGE), budget="10")
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertFalse(view["eligibility"]["adoptable"])
        self.assertEqual([b["code"] for b in view["eligibility"]["blocks"]], ["BUDGET_EXCEEDED"])
        res = self.decide("accept_missing_price")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "INVALID_INPUT"))
        self.assertEqual(res.json()["error"]["details"]["reason"], "DECISION_NOT_APPLICABLE")
        self.assertFalse(self.result()["eligibility"]["adoptable"])

    def test_decision_on_a_stale_questionnaire_version_is_rejected(self):
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        res = self.decide("accept_missing_price", request_version=self.request["version"] + 1)
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "VERSION_CONFLICT"))

    def test_price_retry_rereads_prices_into_a_new_result_version(self):
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        seed_prices(GARLIC)
        res = self.retry_prices()
        self.assertEqual(res.status_code, 201, res.text)
        self.assertEqual(res.json()["result_version"], 2)
        self.assertEqual(self.status(), "result_ready")
        retried = self.result(2)
        self.assertEqual((retried["derived_from_result_version"], retried["mode_label"]), (1, "資料庫菜單"))
        self.assertEqual([d["dish_name"] for d in retried["dishes"]], ["番茄炒蛋", "清炒高麗菜"])
        self.assertEqual(money(retried["prices"]["cost_total"]), Decimal(20))
        self.assertTrue(retried["eligibility"]["adoptable"])
        # 原版本不變
        self.assertIsNone(self.result(1)["prices"]["cost_total"])
        # 同鍵重送回同一新版本
        again = self.retry_prices()
        self.assertEqual((again.status_code, again.json()["result_version"]), (200, 2))

    def test_price_retry_still_missing_needs_a_fresh_acceptance(self):
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        self.assertEqual(self.decide("accept_missing_price").status_code, 201)
        res = self.retry_prices()
        self.assertEqual(res.status_code, 201, res.text)
        self.assertEqual(self.status(), "awaiting_decision")
        retried = self.result(2)
        self.assertFalse(retried["eligibility"]["adoptable"])
        self.assertEqual([d["kind"] for d in retried["eligibility"]["pending_decisions"]], ["accept_missing_price"])

    def test_price_retry_is_refused_when_no_price_is_missing(self):
        self.generate()
        res = self.retry_prices()
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (422, "DECISION_NOT_APPLICABLE"))


class MissingNutritionDecisionTests(ResultTestCase):
    def protein_unknown(self):
        def make():
            from app.services.meal_calculation import IngredientNutrition
            energy, _ = self.reference()

            def calc(rows):
                return [IngredientNutrition({"energy_kcal": energy / 200 * grams, "protein_g": None},
                                            {"sample_id": "FAKE"}) for _, grams in rows]
            return calc
        make.factory = True
        return make

    def test_missing_required_nutrition_is_viewable_but_never_adoptable(self):
        self.generate(calculator=self.protein_unknown())
        self.assertEqual(self.status(), "awaiting_decision")
        view = self.result()
        self.assertIsNone(view["nutrition"]["protein_g"])
        self.assertEqual([d["kind"] for d in view["eligibility"]["pending_decisions"]], ["continue_missing_nutrition"])
        self.assertIn("NUTRITION_INCOMPLETE", [b["code"] for b in view["eligibility"]["blocks"]])
        # P2：缺營養仍可查看每項用量（未驗證）、可計算的成本與具體建議
        self.assertFalse(view["nutrition"]["quantities_verified"])
        self.assertEqual(view["nutrition"]["suggestions"][0]["code"], "NUTRITION_INCOMPLETE")
        first = view["dishes"][0]["ingredients"][0]
        self.assertEqual((Decimal(first["display_amount"]), first["display_unit"]), (Decimal(50), "g"))
        self.assertEqual(view["prices"]["status"], "computed")
        self.assertEqual(money(view["prices"]["known_cost_total"]), money(20))
        self.assertEqual(self.decide("continue_missing_nutrition").status_code, 201)
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertFalse(view["eligibility"]["adoptable"])
        self.assertEqual(view["eligibility"]["pending_decisions"], [])
        self.assertTrue(view["dishes"])

    def test_out_of_range_offers_no_decision(self):
        def doubled():
            energy, protein = self.reference()
            return per_gram_calculator(energy / 100, protein / 100)  # 兩倍 → 超出 ±10%
        doubled.factory = True
        self.generate(calculator=doubled)
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertFalse(view["eligibility"]["adoptable"])
        self.assertEqual(view["eligibility"]["pending_decisions"], [])
        res = self.decide("continue_missing_nutrition")
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
