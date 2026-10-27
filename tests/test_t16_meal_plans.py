"""T16：採用交易與日期歷史查詢（R09、R10、R13；AC01、AC16–AC19、AC29–AC32）。

沿用 T14／T15 的整條流程（問卷 → T10 工作 → generation_results → 結果頁），付費模型與知識服務以假物件取代。
採用經 POST /meal-plans，歷史經 GET /meal-plans?date= 與 /meal-plans/{id} 讀取。
合成食譜每道兩項食材、每項 50 g/n（一位成員 N=1）；報價 300 g／30 元 → 每項採購價 30 元、比例成本 5 元。
"""
import itertools
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
from sqlalchemy import text

from test_t11_recipe_retrieval import BASIL, SOY, seed_recipes
from test_t14_generation import (CABBAGE, EGG, GARLIC, TOMATO, FakeKnowledgeClient, FakeResponder, ai_dish,
                                 knowledge_record, menu, per_gram_calculator)
from test_t15_meal_results import AI_MENU, ALL, ResultTestCase, money, seed_prices

_keys = itertools.count(1)


def new_key(prefix="adopt"):
    return f"{prefix}-{next(_keys):08d}"


class PlanTestCase(ResultTestCase):
    def submit(self, key=None):
        return super().submit(key or new_key("gen"))

    def adopt(self, *, result_version=1, key="adopt-00000001", request_version=None,
              price_exception_decision_id=None, client=None):
        body = {"meal_request_id": self.request["id"], "result_version": result_version,
                "request_version": request_version or self.request["version"]}
        if price_exception_decision_id is not None:
            body["price_exception_decision_id"] = price_exception_decision_id
        return (client or self.client).post("/meal-plans", json=body, headers={"Idempotency-Key": key})

    def plans_on(self, day, client=None):
        res = (client or self.client).get("/meal-plans", params={"date": day})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["meal_plans"]

    def plan(self, plan_id):
        res = self.client.get(f"/meal-plans/{plan_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def other_household(self):
        import db_support
        other = db_support.make_client()
        other.post("/auth/register", json={"email": "other@example.test", "password": "correct-horse-42"})
        household_id = other.post("/household", json={"name": "別家"}).json()["id"]
        return other, household_id


class AdoptionTests(PlanTestCase):
    def test_adopting_an_eligible_result_saves_a_complete_snapshot_and_confirms_the_request(self):
        self.generate()
        res = self.adopt()
        self.assertEqual(res.status_code, 201, res.text)
        plan = res.json()["meal_plan"]
        self.assertEqual((plan["meal_date"], plan["meal_type"], plan["result_version"], plan["request_version"]),
                         ("2026-08-20", "dinner", 1, 1))
        self.assertEqual((plan["mode"], plan["mode_label"], plan["ai_generated"]), ("database_menu", "資料庫菜單", False))
        self.assertEqual(plan["record_label"], "已採用餐食紀錄")
        self.assertEqual(self.status(), "confirmed")

        detail = self.plan(plan["id"])
        # 菜色識別與完整食譜（整餐克數、步驟、來源）
        self.assertEqual([d["dish_name"] for d in detail["dishes"]], ["番茄炒蛋", "清炒高麗菜"])
        first = detail["dishes"][0]
        self.assertTrue(first["standard_dish_id"])
        self.assertEqual(first["aliases"], ["蕃茄炒蛋"])
        self.assertEqual([(i["food_id"], Decimal(i["whole_meal_grams"])) for i in first["ingredients"]],
                         [(TOMATO, Decimal(50)), (EGG, Decimal(50))])
        self.assertIsInstance(first["steps"], list)
        self.assertEqual((first["recipe_source"]["recipe_id"], first["recipe_source"]["source_name"]),
                         ("R01", "合成來源"))
        # 參與項目與目標（含規則版本）
        self.assertEqual(len(detail["participations"]), 1)
        self.assertEqual(Decimal(detail["participations"][0]["n"]), Decimal(1))
        self.assertTrue(detail["targets"]["rule_version"])
        energy, _ = self.reference()
        self.assertEqual(Decimal(detail["nutrition"]["energy_kcal"]), energy)
        # 每人營養：一位成員 N=1，吃到整餐；每日建議含 7 項
        [person] = detail["nutrition"]["per_person"]
        self.assertTrue(person["name"])
        self.assertEqual(Decimal(person["intake"]["energy_kcal"]), energy)
        self.assertEqual(len(person["daily"]), 7)
        # 價格快照與缺值標示
        self.assertEqual(money(detail["prices"]["cost_total"]), Decimal(20))
        self.assertEqual(detail["missing"], {"price_food_ids": [], "price_exception_accepted": False,
                                             "nutrients": {}})
        # 來源／規則版本與昨日比對
        for key in ("result_schema_version", "nutrition_rule_version", "pricing_version", "adoption_rule_version"):
            self.assertTrue(detail["versions"][key], key)
        self.assertEqual(detail["previous_day"]["previous_date"], "2026-08-19")

    def test_trend_lists_adopted_meals_with_nutrition_and_cost_for_this_household_only(self):
        self.generate()
        self.assertEqual(self.adopt().status_code, 201)
        res = self.client.get("/meal-plans/trend", params={"days": 366})
        self.assertEqual(res.status_code, 200, res.text)
        [item] = [i for i in res.json()["items"] if i["meal_date"] == "2026-08-20"]
        energy, _ = self.reference()
        self.assertEqual(item["meal_type"], "dinner")
        self.assertEqual(Decimal(item["energy_kcal"]), energy)
        self.assertTrue(item["target_energy_kcal"])
        self.assertEqual(money(item["cost_total"]), Decimal(20))
        self.assertEqual(money(item["known_cost_total"]), Decimal(20))
        self.assertEqual(self.client.get("/meal-plans/trend", params={"days": 0}).status_code, 422)
        # 截止日（使用者本機今天）之後的餐食是預先安排的未來餐，不列入統計
        before = self.client.get("/meal-plans/trend", params={"days": 30, "until": "2026-08-19"}).json()
        self.assertEqual((before["since"], before["until"], before["items"]), ("2026-07-21", "2026-08-19", []))
        on_day = self.client.get("/meal-plans/trend", params={"days": 1, "until": "2026-08-20"}).json()
        self.assertEqual([i["meal_date"] for i in on_day["items"]], ["2026-08-20"])
        other, _ = self.other_household()
        self.assertEqual(other.get("/meal-plans/trend", params={"days": 366}).json()["items"], [])

    def test_ai_menu_adoption_keeps_ai_label_fallback_reason_and_knowledge_citations(self):
        from knowledge_support import FakeEmbedder
        self.clear_recipes()
        responder = FakeResponder(
            menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], aliases=["蕃茄蛋湯"], method="煮"),
                 ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)])),
            {"items": [{"ingredient_id": TOMATO, "explanation": "番茄含維生素。",
                        "citations": [{"knowledge_id": "K1", "source_version": "v1", "ingredient_id": TOMATO}]}]})
        seed_prices(*ALL)
        self.create_request(dish_count=2)
        self.submit()
        self.run_job(responder, knowledge=FakeKnowledgeClient([knowledge_record("K1", TOMATO, "番茄含維生素（合成）")]),
                     embedder=FakeEmbedder())
        res = self.adopt()
        self.assertEqual(res.status_code, 201, res.text)
        detail = self.plan(res.json()["meal_plan"]["id"])
        self.assertEqual((detail["mode"], detail["mode_label"], detail["ai_generated"], detail["fallback_reason"]),
                         ("ai_generated_menu", "AI 生成菜單", True, "NO_MATCHING_RECIPES"))
        self.assertEqual([(d["dish_name"], d["source"], d["recipe_source"]) for d in detail["dishes"]],
                         [("番茄蛋花湯", "ai", None), ("蒜香高麗菜", "ai", None)])
        self.assertEqual(detail["dishes"][0]["aliases"], ["蕃茄蛋湯"])
        self.assertTrue(detail["dishes"][0]["standard_dish_id"])
        knowledge = detail["knowledge"]
        self.assertEqual(knowledge["status"], "available")
        [item] = knowledge["items"]
        self.assertEqual((item["ingredient_id"], item["citations"][0]["knowledge_id"],
                          item["citations"][0]["source_url"]), (TOMATO, "K1", "https://example.invalid/k/K1"))
        self.assertEqual((detail["versions"]["knowledge_index_version"], detail["versions"]["menu_model"]),
                         ("idx-1", "gpt-5.4-mini-2026-03-17"))


class IdempotencyAndVersionTests(PlanTestCase):
    def test_same_key_same_command_saves_once_and_different_content_is_rejected(self):
        self.generate()
        first = self.adopt()
        self.assertEqual(first.status_code, 201, first.text)
        plan_id = first.json()["meal_plan"]["id"]
        again = self.adopt()
        self.assertEqual((again.status_code, again.json()["meal_plan"]["id"]), (200, plan_id))
        # 換了送出識別碼的重複點擊仍是同一筆
        other_key = self.adopt(key="adopt-00000002")
        self.assertEqual((other_key.status_code, other_key.json()["meal_plan"]["id"]), (200, plan_id))
        # 同鍵不同內容
        clash = self.adopt(result_version=2)
        self.assertEqual((clash.status_code, clash.json()["error"]["code"]), (409, "IDEMPOTENCY_CONFLICT"))
        self.assertEqual(len(self.plans_on("2026-08-20")), 1)

    def test_a_confirmed_request_cannot_adopt_another_result_version(self):
        self.generate()
        self.submit()  # 已有結果的問卷可再生成：新結果版本 2
        self.run_job()
        self.assertEqual(self.adopt(result_version=1).status_code, 201)
        res = self.adopt(result_version=2, key="adopt-00000002")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "VERSION_CONFLICT"))
        self.assertEqual(res.json()["error"]["details"]["reason"], "ALREADY_ADOPTED")
        self.assertEqual([p["result_version"] for p in self.plans_on("2026-08-20")], [1])

    def test_confirmed_request_is_not_overwritten_by_new_generation_or_edits(self):
        self.generate()
        self.assertEqual(self.adopt().status_code, 201)
        res = self.client.post(f"/meal-requests/{self.request['id']}/submit",
                               json={"version": self.request["version"]}, headers={"Idempotency-Key": new_key("gen")})
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (409, "ALREADY_ADOPTED"))
        res = self.client.put(f"/meal-requests/{self.request['id']}",
                              json={"version": self.request["version"], "draft": self.draft(budget="250")})
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]), (409, "ALREADY_ADOPTED"))
        self.assertEqual(self.status(), "confirmed")

    def test_wrong_or_stale_versions_are_rejected_without_history(self):
        self.generate()
        stale = self.adopt(request_version=self.request["version"] + 1)
        self.assertEqual((stale.status_code, stale.json()["error"]["code"]), (409, "VERSION_CONFLICT"))
        self.assertEqual(self.adopt(result_version=9, key="adopt-00000002").status_code, 404)
        # 問卷生成後又修改：舊結果不可採用
        updated = self.client.put(f"/meal-requests/{self.request['id']}",
                                  json={"version": self.request["version"], "draft": self.draft(budget="250")}).json()
        res = self.adopt(request_version=updated["version"], key="adopt-00000003")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "VERSION_CONFLICT"))
        self.assertEqual(self.plans_on("2026-08-20"), [])


class EligibilityTests(PlanTestCase):
    def test_known_cost_over_budget_is_rejected_by_the_backend(self):
        self.generate(budget="10")
        res = self.adopt()
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "BUDGET_EXCEEDED"))
        self.assertEqual(self.status(), "result_ready")
        self.assertEqual(self.plans_on("2026-08-20"), [])

    def test_nutrition_out_of_range_is_rejected(self):
        def too_much():
            energy, protein = self.reference()
            return per_gram_calculator(energy / Decimal(50), protein / Decimal(50))  # 約為目標的 2 倍
        too_much.factory = True
        # 資料庫候選組合全部不可行 → 自動 AI 回退；AI 菜同樣超出 ±10%，可查看但不可採用
        self.generate(calculator=too_much, responder=FakeResponder(AI_MENU))
        res = self.adopt()
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("NUTRITION_OUT_OF_RANGE", [b["code"] for b in res.json()["error"]["details"]["blocks"]])
        self.assertEqual(self.plans_on("2026-08-20"), [])

    def test_missing_price_is_adopted_directly_and_saves_the_exception_flag(self):
        # 2026-10-26 使用者決定：缺價不需接受紀錄即可採用；快照照樣標示缺價食材與預算無法完整驗證
        self.generate(prices=(TOMATO, EGG, CABBAGE))
        res = self.adopt()
        self.assertEqual(res.status_code, 201, res.text)
        detail = self.plan(res.json()["meal_plan"]["id"])
        self.assertTrue(detail["price_exception_accepted"])
        self.assertEqual(detail["missing"]["price_food_ids"], [GARLIC])
        self.assertTrue(detail["missing"]["price_exception_accepted"])
        self.assertIsNone(detail["price_exception"])  # 沒有接受紀錄
        self.assertIsNone(detail["prices"]["cost_total"])
        self.assertEqual(money(detail["prices"]["known_cost_total"]), Decimal(15))
        self.assertEqual(detail["versions"]["adoption_rule_version"], "t16-adoption/v2")

    def test_price_exception_reference_on_a_complete_result_is_rejected(self):
        self.generate()
        res = self.adopt(price_exception_decision_id="00000000-0000-0000-0000-000000000001")
        self.assertEqual((res.status_code, res.json()["error"]["details"]["reason"]),
                         (422, "PRICE_EXCEPTION_NOT_APPLICABLE"))


class IsolationAndTransactionTests(PlanTestCase):
    def test_cross_household_adoption_and_reads_are_rejected(self):
        self.generate()
        other, _ = self.other_household()
        self.assertEqual(self.adopt(client=other).status_code, 404)
        plan_id = self.adopt().json()["meal_plan"]["id"]
        self.assertEqual(other.get(f"/meal-plans/{plan_id}").status_code, 404)
        self.assertEqual(self.plans_on("2026-08-20", client=other), [])
        self.assertEqual(other.get("/meal-plans").json()["dates"], [])

    def test_generation_and_viewing_do_not_add_history(self):
        self.generate()
        self.result()
        self.client.get(f"/meal-requests/{self.request['id']}/results")
        self.assertEqual(self.plans_on("2026-08-20"), [])
        self.assertEqual(self.client.get("/meal-plans").json()["dates"], [])

    def test_failure_inside_the_transaction_rolls_everything_back(self):
        self.generate()
        with patch("app.services.meal_plans.confirm_request", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.adopt()
        self.assertEqual(self.plans_on("2026-08-20"), [])
        self.assertEqual(self.status(), "result_ready")
        # 同一鍵可重新採用：沒有殘留的紀錄或冪等鍵
        self.assertEqual(self.adopt().status_code, 201)

    def test_adoption_makes_no_network_calls(self):
        self.generate()

        def no_network(*args, **kwargs):
            raise AssertionError("採用交易不可呼叫外部服務")
        # 封鎖真實的對外 HTTP（模型、知識服務、價格來源都經 httpx／urllib）；TestClient 走自己的 transport
        with patch.object(httpx.HTTPTransport, "handle_request", no_network), \
                patch("urllib.request.urlopen", no_network):
            res = self.adopt()
        self.assertEqual(res.status_code, 201, res.text)


class PreviousDayRecheckTests(PlanTestCase):
    def test_history_change_after_generation_that_clashes_rejects_the_stale_result(self):
        self.generate()  # 8/20：番茄炒蛋、清炒高麗菜（生成當時 8/19 沒有紀錄）
        self.adopt_yesterday("蕃茄炒蛋")  # 生成後 8/19 採用了同一道菜（以別名記錄）
        # pre-review #4：結果頁讀取時即回報目前的昨日衝突，前端可在按採用前停用並說明
        check = self.result()["previous_day_check"]
        self.assertEqual((check["status"], check["code"], check["conflicting_dishes"]),
                         ("conflict", "PREVIOUS_DAY_DISH_CONFLICT", ["番茄炒蛋"]))
        res = self.adopt()
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "PREVIOUS_DAY_DISH_CONFLICT"))
        self.assertEqual(res.json()["error"]["details"]["conflicting_dishes"], ["番茄炒蛋"])
        self.assertEqual(self.plans_on("2026-08-20"), [])
        self.assertEqual(self.status(), "result_ready")

    def test_history_change_without_clash_is_adopted_with_the_fresh_comparison_saved(self):
        self.generate()
        self.adopt_yesterday("紅蘿蔔炒蛋")
        self.assertEqual(self.result()["previous_day_check"]["status"], "ok")
        res = self.adopt()
        self.assertEqual(res.status_code, 201, res.text)
        previous = self.plan(res.json()["meal_plan"]["id"])["previous_day"]
        self.assertEqual((previous["has_records"], previous["history_changed"]), (False, True))
        self.assertEqual([d["dish_name"] for d in previous["at_adoption"]["excluded_dishes"]], ["紅蘿蔔炒蛋"])


# 8/19 情境：編號排在 R01 之前的合成食譜，沒有昨日紀錄時會先選到它們
SCENARIO_RECIPES = [
    ("R00A", "三杯雞", [], [BASIL, SOY], ["燒"], True),
    ("R00B", "培根炒高麗菜", ["高麗菜炒培根"], [CABBAGE, GARLIC], ["炒"], True),
    ("R00C", "高麗菜炒培根", [], [CABBAGE, SOY], ["炒"], True),  # 另一來源，以別名對應到同一道菜
]
EVERY_PRICE = (*ALL, BASIL, SOY)


class HistoryChainTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        seed_recipes(self.factory, SCENARIO_RECIPES)

    def adopt_new(self, meal_date, **fields):
        self.generate(prices=EVERY_PRICE, meal_date=meal_date, **fields)
        res = self.adopt(key=new_key())
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["meal_plan"]

    @staticmethod
    def names(view):
        return [d["dish_name"] for d in view["dishes"]]

    def test_adopt_0819_then_0820_generation_excludes_yesterday_except_specified(self):
        from app.services import meal_history
        from app.services.recipe_import import standard_dish_id
        # 更早日期與別家的紀錄都不能影響 8/20
        _, other_id = self.other_household()
        with self.factory() as db, db.begin():
            meal_history.record_adopted(db, self.household_id, date(2026, 8, 18), "dinner", mode="database_menu",
                                        dishes=[{"standard_dish_id": standard_dish_id("番茄炒蛋"), "dish_name": "番茄炒蛋"}])
            meal_history.record_adopted(db, other_id, date(2026, 8, 19), "lunch", mode="database_menu",
                                        dishes=[{"standard_dish_id": standard_dish_id("清炒高麗菜"),
                                                 "dish_name": "清炒高麗菜"}])

        # 8/19 晚餐：真實採用「三杯雞、培根炒高麗菜」
        plan_19 = self.adopt_new("2026-08-19")
        self.assertEqual(self.names(plan_19), ["三杯雞", "培根炒高麗菜"])
        # 8/19 另一份只生成、未採用的午餐（含番茄炒蛋）不算歷史
        self.generate(prices=(), meal_date="2026-08-19", meal_type="lunch", specified_dishes=["番茄炒蛋"])
        self.assertIn("番茄炒蛋", self.names(self.result()))

        # 8/20 新規劃：預設排除昨日已採用的兩道菜與其別名（高麗菜炒培根）
        self.generate(prices=(), meal_date="2026-08-20")
        view = self.result()
        self.assertEqual(self.names(view), ["番茄炒蛋", "清炒高麗菜"])
        previous = view["previous_day"]
        self.assertEqual(previous["previous_date"], "2026-08-19")
        self.assertEqual([r["meal_type"] for r in previous["records"]], ["dinner"])
        self.assertEqual([d["dish_name"] for d in previous["excluded_dishes"]], ["三杯雞", "培根炒高麗菜"])
        self.assertEqual(self.adopt(key=new_key()).status_code, 201)

        # 8/20 午餐指定三杯雞：只對三杯雞例外，培根炒高麗菜（含別名）仍排除
        self.generate(prices=(), meal_date="2026-08-20", meal_type="lunch", specified_dishes=["三杯雞"])
        lunch = self.result()
        self.assertIn("三杯雞", self.names(lunch))
        self.assertFalse({"培根炒高麗菜", "高麗菜炒培根"} & set(self.names(lunch)))
        self.assertEqual(lunch["previous_day"]["specified_repeats"], ["三杯雞"])
        # 2026-10-26 起同一天其他餐也排除：8/20 晚餐已採用的兩道菜不會出現在 8/20 午餐
        excluded = lunch["previous_day"]["excluded_dishes"]
        self.assertEqual([(d["dish_name"], d["from"]) for d in excluded],
                         [("培根炒高麗菜", "previous_day"), ("番茄炒蛋", "dinner"), ("清炒高麗菜", "dinner")])
        self.assertFalse({"番茄炒蛋", "清炒高麗菜"} & set(self.names(lunch)))
        self.assertEqual(self.adopt(key=new_key()).status_code, 201)

        # 兩個日期都可回看（各餐別依早午晚排序），日期清單含兩日
        self.assertEqual([self.names(p) for p in self.plans_on("2026-08-19")], [["三杯雞", "培根炒高麗菜"]])
        day_20 = self.plans_on("2026-08-20")
        self.assertEqual([p["meal_type"] for p in day_20], ["lunch", "dinner"])
        self.assertEqual([self.names(p) for p in day_20], [self.names(lunch), ["番茄炒蛋", "清炒高麗菜"]])
        self.assertTrue(all(p["snapshot_available"] for p in day_20))
        dates = [d["meal_date"] for d in self.client.get("/meal-plans").json()["dates"]]
        self.assertEqual(dates, ["2026-08-20", "2026-08-19", "2026-08-18"])

    def test_source_updates_do_not_change_adopted_snapshots(self):
        plan = self.adopt_new("2026-08-19")
        before = self.plan(plan["id"])
        # 來源食譜改名、改網址與用量，價格更新，甚至刪除原問卷（連帶刪除結果與決定）
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE recipes SET recipe_name = '改名三杯雞', source_url = 'https://example.invalid/new' "
                            "WHERE recipe_id = 'R00A'"))
            db.execute(text("UPDATE recipe_ingredients SET weight_g = 999 WHERE recipe_id = 'R00A'"))
        seed_prices(*EVERY_PRICE, price="99")
        self.assertEqual(self.client.delete(f"/meal-requests/{self.request['id']}").status_code, 204)
        after = self.plan(plan["id"])
        self.assertEqual(after, before)
        self.assertEqual(after["dishes"][0]["recipe_source"]["source_url"], "https://example.invalid/R00A")
        # 四項食材各 5 元，醬油為調味料（決策 #44）不計價 → 15 元
        self.assertEqual(money(after["prices"]["cost_total"]), Decimal(15))
