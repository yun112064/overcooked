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
AI_MENU = menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], method="煮"),
               ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)]))


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

    def test_seasonings_are_not_priced_or_listed_but_still_count_for_nutrition(self):
        # 決策 #44：調味料視為家中足量（缺量 0、不查價、成本 0），營養照算
        from unittest.mock import patch
        with patch("app.services.meal_cost.load_seasonings", return_value={GARLIC}):
            self.generate(prices=(TOMATO, EGG, CABBAGE))
        view = self.result()
        self.assertEqual(sorted(i["food_id"] for i in view["prices"]["items"]), sorted((TOMATO, EGG, CABBAGE)))
        self.assertTrue(view["prices"]["price_complete"])
        self.assertEqual(money(view["prices"]["purchase_total"]), Decimal(90))
        garlic = next(i for d in view["dishes"] for i in d["ingredients"] if i["food_id"] == GARLIC)
        self.assertEqual(Decimal(garlic["whole_meal_grams"]), Decimal(50))  # 仍按配方計入營養
        energy, _ = self.reference()
        self.assertEqual(Decimal(view["nutrition"]["energy_kcal"]), energy)
        self.assertTrue(view["eligibility"]["adoptable"], view["eligibility"])

    def test_step_group_references_are_expanded_for_display_and_original_kept(self):
        # 決策 #41：raw_text 開頭的 [X] 分組；步驟原文另存，顯示文字展開代稱
        from sqlalchemy import update

        from app.models import RecipeIngredient, RecipeStep
        with self.factory() as db, db.begin():
            db.execute(update(RecipeIngredient).where(RecipeIngredient.recipe_ingredient_id == "R01-I1")
                       .values(raw_text="[配料]牛番茄 2顆"))
            db.execute(update(RecipeIngredient).where(RecipeIngredient.recipe_ingredient_id == "R01-I2")
                       .values(raw_text="[配料]蛋 3顆"))
            db.add(RecipeStep(step_id="R01-S1", recipe_id="R01", step_no=1, instruction_clean="下所有[配料]快炒即可",
                              method_status="approved", review_status="approved"))
        self.generate()
        first = self.result()["dishes"][0]
        self.assertEqual(first["steps"], ["下所有[配料]快炒即可"])
        self.assertEqual(first["steps_display"], ["下所有配料（牛番茄、蛋）快炒即可"])
        self.assertEqual([i["group"] for i in first["ingredients"]], ["配料", "配料"])

    def test_old_payload_non_gram_amounts_are_shown_as_whole_numbers(self):
        # 決策 #43：舊結果（容量單位存 0.5）讀取時也以整數顯示；步驟沒有顯示文字時沿用原文
        from app.services import meal_results
        payload = {"dishes": [{"standard_dish_id": "s1", "dish_name": "炒蛋", "source": "database",
                               "recipe_id": "R1", "steps": ["打蛋"]}],
                   "calculation": {"ingredients": [
                       {"food_id": "F1", "name": "醬油", "dish_id": "R1", "display_amount": "0.5", "display_unit": "大匙"},
                       {"food_id": "F2", "name": "雞蛋", "dish_id": "R1", "display_amount": "4.72", "display_unit": "顆"},
                       {"food_id": "F3", "name": "高麗菜", "dish_id": "R1", "display_amount": "191", "display_unit": "g"}]}}
        [dish] = meal_results._dishes(payload)
        self.assertEqual([(Decimal(i["display_amount"]), i["display_unit"]) for i in dish["ingredients"]],
                         [(Decimal(1), "大匙"), (Decimal(5), "顆"), (Decimal(191), "g")])
        self.assertEqual(dish["steps_display"], ["打蛋"])

    def test_seasonings_are_shown_in_spoons_from_whole_meal_grams(self):
        # 調味料畫面用量由整餐克數換成量匙（原單位 g、ml、大匙都一樣）；克數欄位不變，計數單位與整顆香料照舊
        from app.services import meal_results
        payload = {"dishes": [{"standard_dish_id": "s1", "dish_name": "滷肉", "source": "database",
                               "recipe_id": "R1", "steps": []}],
                   "calculation": {"ingredients": [
                       {"food_id": "FOOD000691", "name": "食鹽", "dish_id": "R1", "whole_meal_grams": "3.6",
                        "display_amount": "4", "display_unit": "g"},
                       {"food_id": "FOOD001541", "name": "醬油", "dish_id": "R1", "whole_meal_grams": "27",
                        "display_amount": "1", "display_unit": "大匙"},
                       {"food_id": "FOOD000693", "name": "香油", "dish_id": "R1", "whole_meal_grams": "0.4",
                        "display_amount": "1", "display_unit": "ml"},
                       {"food_id": "FOOD000007", "name": "八角", "dish_id": "R1", "whole_meal_grams": "2",
                        "display_amount": "2", "display_unit": "顆"},
                       {"food_id": "FOOD000289", "name": "白胡椒粉", "dish_id": "R1", "whole_meal_grams": "2",
                        "display_amount": "1", "display_unit": "粒"}]}}
        [dish] = meal_results._dishes(payload)
        shown = [(i["display_amount"] and Decimal(i["display_amount"]), i["display_unit"], i["display_text"])
                 for i in dish["ingredients"]]
        self.assertEqual(shown, [(Decimal("0.75"), "小匙", None), (Decimal("1.5"), "大匙", None),
                                 (None, None, "少許"), (Decimal(2), "顆", None), (Decimal(1), "粒", None)])
        self.assertEqual(dish["ingredients"][1]["whole_meal_grams"], "27")

    def test_spoon_steps_round_up_to_the_next_measure(self):
        from app.services.seasonings import spoon_display
        salt = lambda grams: spoon_display("FOOD000691", grams, "g")  # 5 g／小匙
        self.assertEqual(salt(Decimal("0.5"))["display_text"], "少許")
        self.assertEqual((salt(Decimal("14.6"))["display_amount"], salt(Decimal("14.6"))["display_unit"]),
                         (Decimal(1), "大匙"))
        self.assertEqual((salt(Decimal(238))["display_amount"], salt(Decimal(238))["display_unit"]),
                         (Decimal(1), "量杯"))
        self.assertIsNone(spoon_display("FOOD000691", None, "g"))
        self.assertIsNone(spoon_display("NOT_A_SEASONING", Decimal(5), "g"))

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
    def test_missing_price_is_adoptable_without_a_decision(self):
        # 2026-10-26 使用者決定：缺價不擋採用、不需確認；畫面照樣列出查無價格的食材與已知部分
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        prices = view["prices"]
        garlic = next(i for i in prices["items"] if i["food_id"] == GARLIC)
        self.assertEqual((garlic["price_status"], garlic["purchase_price"], garlic["cost"]), ("unknown", None, None))
        self.assertTrue(garlic["missing_reason"])
        self.assertEqual((prices["purchase_total"], prices["cost_total"]), (None, None))
        self.assertEqual((money(prices["known_purchase_total"]), money(prices["known_cost_total"])),
                         (Decimal(90), Decimal(15)))
        eligibility = view["eligibility"]
        self.assertTrue(eligibility["adoptable"])
        self.assertTrue(eligibility["price_exception_accepted"])
        self.assertEqual((eligibility["blocks"], eligibility["pending_decisions"]), ([], []))
        # 已不需要接受缺價：送出決定會被拒絕
        res = self.decide("accept_missing_price")
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (422, "DECISION_NOT_APPLICABLE"))

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

    def run_retry(self, kind, handler):
        from app.jobs.worker import run_once
        from sqlalchemy import text
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE background_jobs SET available_at = now() WHERE status = 'queued'"))
        return run_once(self.factory, "worker-a", {kind: handler})

    def lookup(self, *found, error=None):
        """取代 T02 定向查價：記錄被要求的 FOOD 編號，並為 found 寫入價格快照。"""
        calls = []

        def lookup(food_ids):
            calls.append(list(food_ids))
            if error:
                raise error
            seed_prices(*found)
            return {"status": "completed", "auto_adopted": len(found), "estimates": 0, "missing": 0}
        lookup.calls = calls
        return lookup

    def test_price_retry_runs_a_targeted_lookup_and_produces_a_new_version(self):
        from app.services.result_retry import make_price_retry_handler
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        res = self.retry_prices()
        self.assertEqual(res.status_code, 202, res.text)
        job = res.json()["job"]
        self.assertEqual((job["kind"], job["status"], job["source_result_version"]), ("price_retry", "queued", 1))
        # 查價中可辨識；問卷狀態不變、原結果仍可查看與採用
        self.assertEqual(self.result()["retries"]["price"]["status"], "queued")
        self.assertEqual(self.status(), "result_ready")
        again = self.retry_prices()  # 同鍵重送：同一工作
        self.assertEqual((again.status_code, again.json()["job"]["id"]), (200, job["id"]))
        lookup = self.lookup(GARLIC)
        self.run_retry("price_retry", make_price_retry_handler(price_lookup=lookup))
        self.assertEqual(lookup.calls, [[GARLIC]])  # 只查缺價食材
        self.assertEqual(self.job(job["id"])["status"], "succeeded")
        self.assertEqual(self.status(), "result_ready")
        retried = self.result(2)
        self.assertEqual((retried["derived_from_result_version"], retried["mode_label"]), (1, "資料庫菜單"))
        self.assertEqual([d["dish_name"] for d in retried["dishes"]], ["番茄炒蛋", "清炒高麗菜"])
        self.assertEqual(money(retried["prices"]["cost_total"]), Decimal(20))
        self.assertTrue(retried["eligibility"]["adoptable"])
        self.assertEqual(retried["price_retry"]["price_source"], "t02_targeted_lookup")
        original = self.result(1)
        self.assertIsNone(original["prices"]["cost_total"])  # 原版本不變
        self.assertEqual((original["retries"]["price"]["status"], original["retries"]["price"]["produced_result_version"]),
                         ("succeeded", 2))
        self.assertIn("retry_missing_price", [d["kind"] for d in original["eligibility"]["decisions"]])

    def test_price_retry_still_missing_stays_adoptable(self):
        from app.services.result_retry import make_price_retry_handler
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        self.assertEqual(self.retry_prices().status_code, 202)
        self.run_retry("price_retry", make_price_retry_handler(price_lookup=self.lookup()))
        self.assertEqual(self.status(), "result_ready")
        retried = self.result(2)
        self.assertTrue(retried["eligibility"]["adoptable"])
        self.assertTrue(retried["eligibility"]["price_exception_accepted"])
        self.assertEqual(retried["eligibility"]["pending_decisions"], [])

    def test_failed_lookup_is_visible_and_keeps_the_original_result(self):
        from app.services.result_retry import make_price_retry_handler
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        job_id = self.retry_prices().json()["job"]["id"]
        self.run_retry("price_retry", make_price_retry_handler(price_lookup=self.lookup(error=RuntimeError("down"))))
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "PRICE_LOOKUP_FAILED"))
        view = self.result()
        self.assertEqual((view["retries"]["price"]["status"], view["retries"]["price"]["error"]["code"]),
                         ("failed", "PRICE_LOOKUP_FAILED"))
        self.assertEqual(self.status(), "result_ready")
        self.assertEqual(len(self.results()), 1)
        # 失敗後可用新的識別碼再試
        self.assertEqual(self.retry_prices(key="retry-00000002").status_code, 202)

    def test_price_retry_is_rate_limited_per_household(self):
        from unittest.mock import patch
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        with patch("app.core.config.settings.price_retry_hourly_limit", 1):
            self.assertEqual(self.retry_prices().status_code, 202)
            from app.services import jobs
            from sqlalchemy import text
            with self.factory() as db, db.begin():  # 第一個工作已結束（失敗），仍計入速率限制
                db.execute(text("UPDATE background_jobs SET status = 'failed' WHERE kind = :k"), {"k": jobs.PRICE_RETRY})
            res = self.retry_prices(key="retry-00000002")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (429, "RATE_LIMITED"))

    def test_price_retry_is_refused_when_no_price_is_missing(self):
        self.generate()
        res = self.retry_prices()
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (422, "DECISION_NOT_APPLICABLE"))


class KnowledgeRetryTests(ResultTestCase):
    """pre-review #3：知識服務故障時「只重試知識說明」，不改菜單／份量／TFDA 數值，經 T10 額度。"""

    def retry_knowledge(self, *, version=1, key="kretry-00000001"):
        return self.client.post(f"/meal-requests/{self.request['id']}/results/{version}/knowledge-retry",
                                json={"request_version": self.request["version"]}, headers={"Idempotency-Key": key})

    def test_only_the_explanation_is_regenerated_into_a_new_version(self):
        from knowledge_support import FakeEmbedder
        from app.jobs.worker import run_once
        from app.services.result_retry import make_knowledge_retry_handler
        from test_t14_generation import knowledge_record
        self.generate(knowledge=FakeKnowledgeClient(error="UNREACHABLE"))
        first = self.result()
        self.assertTrue(first["knowledge"]["retry_available"])
        res = self.retry_knowledge()
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual(res.json()["job"]["kind"], "knowledge_retry")
        self.assertEqual(self.result()["retries"]["knowledge"]["status"], "queued")
        # 生成面板只追蹤生成工作：可依種類篩選，重試工作不會被當成「生成中」
        jobs = self.client.get("/jobs", params={"meal_request_id": self.request["id"], "kind": "meal_generation"})
        self.assertEqual({j["kind"] for j in jobs.json()["jobs"]}, {"meal_generation"})
        self.assertEqual(self.status(), "result_ready")
        client = FakeKnowledgeClient([knowledge_record("K1", TOMATO, "番茄含維生素（合成）")])
        responder = FakeResponder({"items": [{"ingredient_id": TOMATO, "explanation": "番茄含維生素。", "citations": [
            {"knowledge_id": "K1", "source_version": "v1", "ingredient_id": TOMATO}]}]})
        handler = make_knowledge_retry_handler(responder=responder, knowledge_client=client, embedder=FakeEmbedder())
        run_once(self.factory, "worker-a", {"knowledge_retry": handler})
        self.assertEqual(client.calls, [sorted(ALL)])  # 同一結果的最終食材
        second = self.result(2)
        self.assertEqual(second["derived_from_result_version"], 1)
        self.assertEqual(second["knowledge"]["status"], "available")
        self.assertEqual(second["knowledge"]["items"][0]["ingredient_id"], TOMATO)
        for key in ("dishes", "nutrition", "prices"):  # 菜單、份量、營養、價格完全不變
            self.assertEqual(second[key], first[key], key)
        self.assertEqual(self.result()["knowledge"]["status"], "unavailable")  # 原版本不變
        job_id = res.json()["job"]["id"]
        purposes = [(r.purpose, r.status) for r in self.reservations() if str(r.job_id) == job_id]
        self.assertEqual(purposes, [("knowledge_query_embedding", "settled"), ("knowledge_explanation", "settled")])

    def test_knowledge_retry_is_refused_when_knowledge_is_merely_missing(self):
        self.generate(knowledge=FakeKnowledgeClient([]))  # 查無知識（not_found）不是故障
        res = self.retry_knowledge()
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (422, "RETRY_NOT_APPLICABLE"))


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
        # 決策 #47：缺資料的資料庫組合不作為結果 → 轉 AI；AI 菜同樣缺蛋白質時結果仍可查看、需使用者決定
        self.generate(calculator=self.protein_unknown(), responder=FakeResponder(AI_MENU))
        self.assertEqual(self.status(), "awaiting_decision")
        self.assertEqual(self.result()["mode"], "ai_generated_menu")
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

    def test_unknown_nutrient_reports_known_part_and_missing_ingredients(self):
        """決策 #53：值為 null 時提供已知部分合計與缺值食材，畫面顯示「未知（已知 X，缺：A）」，不以 0 代替。"""
        def make():
            from app.services.meal_calculation import IngredientNutrition
            energy, protein = self.reference()

            def calc(rows):
                return [IngredientNutrition({"energy_kcal": energy / 200 * g,
                                             "protein_g": None if ln.food_id == CABBAGE else protein / 200 * g,
                                             "sodium_mg": Decimal(1) * g},
                                            {"sample_id": "FAKE"}) for ln, g in rows]
            return calc
        make.factory = True
        self.clear_recipes()
        self.generate(calculator=make, responder=FakeResponder(AI_MENU))
        nutrition = self.result()["nutrition"]
        _, protein = self.reference()
        self.assertIsNone(nutrition["protein_g"])
        self.assertEqual(nutrition["missing_by_nutrient"]["protein_g"], ["高麗菜"])
        # 番茄、雞蛋、大蒜各 50 g（N=1）→ 已知蛋白質 = 150/200 × 參考值
        self.assertEqual(Decimal(nutrition["known_partial"]["protein_g"]).quantize(Decimal("0.01")),
                         (protein * 150 / 200).quantize(Decimal("0.01")))
        self.assertNotIn("energy_kcal", nutrition["missing_by_nutrient"])
        self.assertEqual(Decimal(nutrition["total_n"]), Decimal(1))

    def test_out_of_range_offers_no_decision(self):
        def doubled():
            energy, protein = self.reference()
            # 五倍 → 整道縮到最小倍率 0.4（決策 #48）仍超出 ±10%
            return per_gram_calculator(energy / 40, protein / 40)
        doubled.factory = True
        # 資料庫候選組合全部不可行 → 自動 AI 回退；AI 菜同樣超出 ±10%
        self.generate(calculator=doubled, responder=FakeResponder(AI_MENU))
        self.assertEqual(self.status(), "result_ready")
        view = self.result()
        self.assertFalse(view["eligibility"]["adoptable"])
        self.assertEqual(view["eligibility"]["pending_decisions"], [])
        res = self.decide("continue_missing_nutrition")
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
