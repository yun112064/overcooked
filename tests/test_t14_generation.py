"""T14：資料庫菜單生成、AI 自動回退與知識說明（R05、R12、R13；AC07–AC09、AC29、AC31、AC32）。

整條工作流程經 T10 工作程序（run_once）與測試資料庫執行；付費模型、向量化與知識服務以假物件
取代（不呼叫真實付費 API），額度預留／結算仍寫入資料庫。食譜與知識皆為合成資料。
營養以假 calculator 計算（每克固定含量），讓預期值可由人工推算；TFDA 管線另由 T12 測試。
"""
import json
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select

from db_support import make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all
from test_t11_recipe_retrieval import BASIL, BOKCHOY, CABBAGE, CARROT, EGG, GARLIC, SOY, TOMATO, seed_recipes, RECIPES

PASSWORD = "correct-horse-42"
ALIASES = {r[1]: r[2] for r in RECIPES}  # 已採用紀錄保存的別名（與 T11 合成食譜相同）
MODEL = "gpt-5.4-mini-2026-03-17"
FOOD_NAMES = {EGG: "雞蛋", TOMATO: "番茄", CABBAGE: "高麗菜", GARLIC: "大蒜", BOKCHOY: "青江菜", CARROT: "胡蘿蔔",
              BASIL: "九層塔", SOY: "醬油"}


class FakeResponder:
    """取代 OpenAI Responses API：依序回傳預先排定的回應，並記錄每次呼叫。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def respond(self, **kwargs):
        from app.services.llm_client import StructuredResponse
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, StructuredResponse):
            return outcome, {"input_tokens": 900, "output_tokens": 300}
        text = outcome if isinstance(outcome, str) else json.dumps(outcome, ensure_ascii=False)
        return StructuredResponse("completed", output_text=text), {"input_tokens": 900, "output_tokens": 300}


def ai_dish(name, ingredients, aliases=(), method="炒", steps=("熱鍋下油", "加入食材拌炒至熟")):
    return {"dish_name": name, "aliases": list(aliases), "cooking_method": method, "steps": list(steps),
            "ingredients": [{"name": FOOD_NAMES.get(food, food), "food_id": food if food in FOOD_NAMES else None,
                             "grams_per_n": grams, "weight_basis": "raw"} for food, grams in ingredients]}


def menu(*dishes):
    return {"dishes": list(dishes)}


def per_gram_calculator(energy_per_g, protein_per_g):
    """假營養計算：每克固定熱量／蛋白質；回傳形狀與 T12 Calculator 相同。"""
    from app.services.meal_calculation import IngredientNutrition

    def calc(rows):
        return [IngredientNutrition({"energy_kcal": energy_per_g * grams, "protein_g": protein_per_g * grams},
                                    {"sample_id": "FAKE"}) for _, grams in rows]
    return calc


class FakeKnowledgeClient:
    def __init__(self, records=(), error=None, index_version="idx-1"):
        self.records, self.error, self.index_version = list(records), error, index_version
        self.calls = []

    def search(self, food_ids, vector, model, *, limit_per_ingredient):
        self.calls.append(list(food_ids))
        if self.error:
            from app.services.knowledge import KnowledgeUnavailable
            raise KnowledgeUnavailable(self.error)
        return {"records": [r for r in self.records if r["ingredient_id"] in food_ids],
                "index_version": self.index_version}


def knowledge_record(kid, food_id, excerpt):
    return {"knowledge_id": kid, "source_version": "v1", "ingredient_id": food_id, "excerpt": excerpt,
            "source_url": f"https://example.invalid/k/{kid}", "rights": "合成測試資料",
            "conditions": "一般成人；合成情境", "reviewer": "審核者", "reviewed_at": "2026-10-01T00:00:00Z",
            "distance": 0.1}


@requires_db
class GenerationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.factory = session_factory()
        seed_recipes(self.factory)
        self.client = make_client()
        self.client.post("/auth/register", json={"email": "owner@example.test", "password": PASSWORD})
        self.household_id = self.client.post("/household", json={"name": "測試家庭"}).json()["id"]
        self.member = self.client.post("/members", json={
            "display_name": "成員A", "birth_date": "1996-03-01", "equation_sex": "M", "weight_kg": "70",
            "activity_category": "sedentary", "goal": "maintain", "portion": "1",
            "allergen_ingredient_ids": self.allergens(), "avoided_ingredient_ids": []}).json()
        patcher = patch("app.core.config.settings.openai_api_key", None)  # 保險：絕不碰真實金鑰
        patcher.start()
        self.addCleanup(patcher.stop)

    def allergens(self):
        return []

    def draft(self, **fields):
        base = {"meal_date": "2026-08-20", "meal_type": "dinner", "budget": "300", "staple": "不需要主食",
                "dish_count": 2, "participants": [{"member_id": self.member["id"]}]}
        return {**base, **fields}

    def create_request(self, **fields):
        self.request = self.client.post("/meal-requests", json=self.draft(**fields)).json()
        return self.request

    def submit(self, key="key-00000001"):
        res = self.client.post(f"/meal-requests/{self.request['id']}/submit", json={"version": self.request["version"]},
                               headers={"Idempotency-Key": key})
        self.assertEqual(res.status_code, 202, res.text)
        return res.json()["job"]["id"]

    def reference(self):
        """以本餐 T03 目標計算每 1n 參考值（假 calculator 依此設定每克含量）。"""
        preview = self.client.get(f"/meal-requests/{self.request['id']}").json()["targets_preview"]
        n = Decimal(preview["total_n"])
        return Decimal(preview["target"]["energy_kcal"]) / n, Decimal(preview["target"]["protein_g"]) / n

    def calculator_for(self, grams_per_n=Decimal(200)):
        """每 1n 共 grams_per_n 克時，熱量與蛋白質恰為參考值。"""
        energy, protein = self.reference()
        return per_gram_calculator(energy / grams_per_n, protein / grams_per_n)

    def run_job(self, responder=None, knowledge=None, embedder=None, calculator=None, *, available=True):
        from app.jobs.worker import run_once
        from app.services.generation import make_generation_handler
        handler = make_generation_handler(responder=responder, knowledge_client=knowledge, embedder=embedder,
                                          calculator=calculator or self.calculator_for())
        if available:
            from sqlalchemy import text
            with self.factory() as db, db.begin():
                db.execute(text("UPDATE background_jobs SET available_at = now() WHERE status = 'queued'"))
        return run_once(self.factory, "worker-a", {"meal_generation": handler})

    def job(self, job_id):
        return self.client.get(f"/jobs/{job_id}").json()

    def results(self):
        from app.db.models import GenerationResult
        with self.factory() as db:
            return db.scalars(select(GenerationResult).order_by(GenerationResult.result_version)).all()

    def reservations(self):
        from app.db.models import UsageReservation
        with self.factory() as db:
            return db.scalars(select(UsageReservation).order_by(UsageReservation.created_at)).all()

    def clear_recipes(self):
        from sqlalchemy import text
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE recipes SET is_eligible = false"))

    def adopt_yesterday(self, *names):
        from app.services import meal_history
        from app.services.recipe_import import standard_dish_id
        with self.factory() as db, db.begin():
            meal_history.record_adopted(
                db, self.household_id, date(2026, 8, 19), "dinner", mode="database_menu",
                dishes=[{"standard_dish_id": standard_dish_id(n), "dish_name": n, "aliases": ALIASES.get(n, [])}
                        for n in names])


class DatabaseMenuTests(GenerationTestCase):
    def test_database_menu_uses_only_retrieved_dishes_without_calling_the_model(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(responder.calls, [])
        [row] = self.results()
        payload = row.payload
        self.assertEqual((row.mode, row.fallback_reason, row.result_version, row.request_version),
                         ("database_menu", None, 1, 1))
        self.assertEqual(payload["mode_label"], "資料庫菜單")
        self.assertEqual([(d["recipe_id"], d["source"]) for d in payload["dishes"]],
                         [("R01", "database"), ("R02", "database")])
        self.assertEqual([d["dish_name"] for d in payload["dishes"]], ["番茄炒蛋", "清炒高麗菜"])
        self.assertEqual(payload["query_source"]["status"], "database_menu")
        # 營養由後端計算：200 g/n 恰為參考值 → 可採用（以 T12 計算）
        self.assertEqual(payload["calculation"]["mode"], "database")
        self.assertTrue(payload["calculation"]["adoptable"], payload["calculation"]["block_reasons"])
        self.assertEqual(payload["previous_day"]["previous_date"], "2026-08-19")
        self.assertEqual(payload["evidence"]["schema_version"], "generation_result/v1")
        self.assertIsNone(payload["evidence"]["menu_model"])
        # 工作摘要指向結果版本；未呼叫任何付費服務
        self.assertEqual(job["result_version"], 1)
        self.assertEqual(self.reservations(), [])
        # 未寫入價格快照 → 全部缺價，T15 起問卷等待使用者決定（result_ready 不代表可採用）
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()["status"],
                         "awaiting_decision")


class AiFallbackTests(GenerationTestCase):
    def valid_menu(self):
        # 每 1n 共 200 g（4 × 50 g）→ 假 calculator 恰為參考值
        return menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], aliases=["蕃茄蛋湯"], method="煮"),
                    ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)]))

    def test_no_feasible_database_menu_falls_back_to_ai_without_consent(self):
        self.clear_recipes()  # 資料庫正常但沒有合格食譜 → NO_MATCHING_RECIPES
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder(self.valid_menu())
        self.run_job(responder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        payload = row.payload
        self.assertEqual((row.mode, row.fallback_reason), ("ai_generated_menu", "NO_MATCHING_RECIPES"))
        self.assertEqual(payload["mode_label"], "AI 生成菜單")
        # 工作查詢即可看到模式標示（結果頁由 T15 提供）
        summary = self.job(job_id)["result_summary"]
        self.assertEqual((summary["mode_label"], summary["result_version"], summary["fallback_reason"]),
                         ("AI 生成菜單", 1, "NO_MATCHING_RECIPES"))
        self.assertEqual(payload["query_source"]["code"], "NO_FEASIBLE_DATABASE_MENU")
        # AI 菜不捏造資料庫引用；保存菜色識別
        from app.services.recipe_import import standard_dish_id
        self.assertEqual([(d["source"], d["recipe_id"], d["standard_dish_id"]) for d in payload["dishes"]],
                         [("ai", None, standard_dish_id("番茄蛋花湯")), ("ai", None, standard_dish_id("蒜香高麗菜"))])
        self.assertEqual(payload["dishes"][0]["aliases"], ["蕃茄蛋湯"])
        # 營養由後端 T12（AI 模式只驗證）計算
        self.assertEqual(payload["calculation"]["mode"], "ai")
        self.assertTrue(payload["calculation"]["adoptable"])
        self.assertEqual(payload["evidence"]["menu_model"], MODEL)
        self.assertEqual(payload["evidence"]["menu_prompt_version"], "t14-menu-prompt-v1")
        # Structured Outputs：送出 strict schema；問卷文字只在 input
        [call] = responder.calls
        self.assertEqual(call["model"], MODEL)
        self.assertEqual(call["schema"]["additionalProperties"], False)
        self.assertNotIn("不需要主食", call["instructions"])
        self.assertIn("不需要主食", call["input_text"])
        # 付費呼叫已預留並結算
        [usage] = self.reservations()
        self.assertEqual((usage.status, usage.purpose, usage.model), ("settled", "menu_generation", MODEL))
        self.assertEqual(payload["evidence"]["paid_call_keys"], [usage.call_key])

    def test_specified_dish_missing_from_database_triggers_ai_and_must_appear(self):
        self.create_request(dish_count=2, specified_dishes=["麻婆豆腐"])
        job_id = self.submit()
        mapo = ai_dish("麻婆豆腐", [(GARLIC, 100), (SOY, 50), (EGG, 50)], method="燒")
        responder = FakeResponder(menu(ai_dish("番茄炒蛋", [(TOMATO, 100), (EGG, 100)]),
                                       ai_dish("蒜香高麗菜", [(CABBAGE, 100), (GARLIC, 100)])),
                                  menu(mapo, ai_dish("蒜香高麗菜", [(CABBAGE, 150), (GARLIC, 150)])))
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("queued", "GENERATION_FAILED"))
        self.assertIn("SPECIFIED_DISH_MISSING", job["error"]["message"])
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["attempts"]), ("succeeded", 2))
        [row] = self.results()
        self.assertEqual(row.fallback_reason, "SPECIFIED_DISH_NOT_IN_DATABASE")
        self.assertEqual([d["specified"] for d in row.payload["dishes"]], [True, False])
        self.assertEqual(row.payload["evidence"]["attempt"], 2)
        # 營養不符只讓結果不可採用（後端 T12 判定），不是無效候選、不再重送
        self.assertFalse(row.payload["calculation"]["adoptable"])
        self.assertIn("NO_FEASIBLE_BASELINE", {r["code"] for r in row.payload["calculation"]["block_reasons"]})
        # 兩次嘗試各一筆已結算的預留，呼叫鍵不同
        rows = self.reservations()
        self.assertEqual([r.status for r in rows], ["settled", "settled"])
        self.assertEqual(len({r.call_key for r in rows}), 2)

    def test_previous_day_exclusion_still_applies_in_ai_mode_except_specified(self):
        # 6 道合格菜中 4 道昨天吃過，要 3 道 → 去重後不足 → AI；指定的番茄炒蛋可重複
        self.adopt_yesterday("番茄炒蛋", "清炒高麗菜", "蒜炒青江菜", "紅蘿蔔炒蛋")
        self.create_request(dish_count=3, specified_dishes=["番茄炒蛋"], available_ingredient_ids=[])
        self.clear_recipes()
        job_id = self.submit()
        tomato_egg = ai_dish("番茄炒蛋", [(TOMATO, 30), (EGG, 30)])
        responder = FakeResponder(
            menu(tomato_egg, ai_dish("炒高麗菜", [(CABBAGE, 50)]), ai_dish("九層塔煎蛋", [(BASIL, 20), (EGG, 70)])),
            menu(tomato_egg, ai_dish("胡蘿蔔絲", [(CARROT, 70)]), ai_dish("九層塔煎蛋", [(BASIL, 20), (EGG, 50)])))
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual(job["status"], "queued")
        self.assertIn("PREVIOUS_DAY_DISH_CONFLICT", job["error"]["message"])  # 「炒高麗菜」是昨日清炒高麗菜的別名
        # 昨日排除清單已放進模型輸入（但最終仍由後端檢查）
        sent = json.loads(responder.calls[0]["input_text"])
        self.assertNotIn("番茄炒蛋", [d["dish_name"] for d in sent["avoid_dishes"]])
        self.assertIn("清炒高麗菜", [d["dish_name"] for d in sent["avoid_dishes"]])
        self.run_job(responder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.mode, "ai_generated_menu")
        context = row.payload["previous_day"]
        self.assertEqual(context["previous_date"], "2026-08-19")
        from app.services.recipe_import import standard_dish_id
        self.assertEqual(context["specified_exceptions"], [standard_dish_id("番茄炒蛋")])
        # 清炒高麗菜與蒜炒青江菜共用別名「炒青菜」，T11 視為同一道並合併別名
        self.assertEqual([d["dish_name"] for d in context["excluded_dishes"]], ["清炒高麗菜", "紅蘿蔔炒蛋"])

    def test_refusal_is_retried_once_then_reported_without_more_calls(self):
        from app.services.llm_client import StructuredResponse
        self.clear_recipes()
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder(StructuredResponse("refused", refusal="無法協助"),
                                  StructuredResponse("refused", refusal="無法協助"), self.valid_menu())
        for _ in range(3):
            self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["attempts"], job["error"]["code"]), ("failed", 2, "GENERATION_FAILED"))
        self.assertIn("MODEL_REFUSED", job["error"]["message"])
        self.assertEqual(len(responder.calls), 2)  # 不無限重送
        self.assertEqual(self.results(), [])
        self.assertEqual([r.status for r in self.reservations()], ["settled", "settled"])
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}").json()["draft"]["dish_count"], 2)

    def test_truncated_and_malformed_outputs_are_rejected(self):
        from app.services.llm_client import StructuredResponse
        self.clear_recipes()
        self.create_request(dish_count=2)
        job_id = self.submit()
        broken = self.valid_menu()
        broken["dishes"][0]["price"] = 30  # schema 外欄位（模型自報價格）
        responder = FakeResponder(StructuredResponse("incomplete", incomplete_reason="max_output_tokens"), broken)
        self.run_job(responder)
        self.assertIn("OUTPUT_TRUNCATED", self.job(job_id)["error"]["message"])
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("INVALID_FORMAT", job["error"]["message"])
        self.assertEqual(self.results(), [])

    def test_unknown_ingredient_and_forbidden_ingredient_are_rejected(self):
        self.clear_recipes()
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder(
            menu(ai_dish("龍蝦沙拉", [("龍蝦", 100)]), ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)])),
            menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), ("FOOD999999", 50)]),
                 ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)])))
        self.run_job(responder)
        self.assertIn("INGREDIENT_UNRESOLVED", self.job(job_id)["error"]["message"])
        self.run_job(responder)
        self.assertEqual(self.job(job_id)["status"], "failed")
        self.assertEqual(self.results(), [])

    def test_model_ingredient_id_contradicting_its_name_is_rejected(self):
        self.clear_recipes()
        self.create_request(dish_count=2)
        job_id = self.submit()
        bad = self.valid_menu()
        bad["dishes"][0]["ingredients"][0]["food_id"] = CARROT  # 名稱「番茄」卻填胡蘿蔔編號
        self.run_job(FakeResponder(bad))
        self.assertIn("INGREDIENT_ID_MISMATCH", self.job(job_id)["error"]["message"])

    def test_missing_model_service_fails_clearly_without_paid_calls(self):
        self.clear_recipes()
        self.create_request(dish_count=2)
        job_id = self.submit()
        self.run_job(None)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "GENERATION_FAILED"))
        self.assertIn("未設定", job["error"]["message"])
        self.assertEqual(self.reservations(), [])


class AllergyTests(GenerationTestCase):
    def allergens(self):
        return [EGG]

    def test_ai_candidate_with_allergen_is_rejected_even_under_synonym(self):
        self.clear_recipes()
        self.create_request(dish_count=1)
        job_id = self.submit()
        responder = FakeResponder(menu(ai_dish("蛋炒飯", [("蛋", 100), (GARLIC, 100)])),
                                  menu(ai_dish("蒜炒高麗菜", [(CABBAGE, 150), (GARLIC, 50)])))
        self.run_job(responder)
        job = self.job(job_id)
        self.assertIn("DIETARY_CONFLICT", job["error"]["message"])
        forbidden = json.loads(responder.calls[0]["input_text"])["forbidden_ingredients"]
        self.assertIn({"food_id": EGG, "name": "雞蛋"}, forbidden)
        self.run_job(responder)
        [row] = self.results()
        self.assertIn(EGG, row.payload["restrictions"]["excluded_food_ids"])
        self.assertNotIn(EGG, row.payload["dishes"][0]["food_ids"])


class KnowledgeExplanationTests(GenerationTestCase):
    """資料庫菜單 R01 番茄炒蛋＋R02 清炒高麗菜 → 最終食材：番茄、雞蛋、高麗菜、大蒜。"""

    def setUp(self):
        super().setUp()
        from knowledge_support import FakeEmbedder
        self.embedder = FakeEmbedder()
        self.records = [knowledge_record("K1", TOMATO, "番茄含維生素（合成）"),
                        knowledge_record("K2", EGG, "雞蛋含蛋白質（合成）")]

    def cite(self, kid, food, version="v1"):
        return {"knowledge_id": kid, "source_version": version, "ingredient_id": food}

    def test_only_citations_matching_retrieved_knowledge_are_kept(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        client = FakeKnowledgeClient(self.records)
        responder = FakeResponder({"items": [
            {"ingredient_id": TOMATO, "explanation": "番茄含維生素。", "citations": [self.cite("K1", TOMATO)]},
            {"ingredient_id": EGG, "explanation": "雞蛋可護眼。", "citations": [self.cite("K1", TOMATO)]},  # 錯食材
            {"ingredient_id": CABBAGE, "explanation": "高麗菜很健康。", "citations": [self.cite("K9", CABBAGE)]},  # 虛構
            {"ingredient_id": EGG, "explanation": "雞蛋含蛋白質。", "citations": [self.cite("K2", EGG, "v0")]},  # 舊版
        ]})
        self.run_job(responder, knowledge=client, embedder=self.embedder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        payload = row.payload
        # 知識在菜單確定後才依最終食材查詢；不改菜單
        self.assertEqual(client.calls, [sorted([TOMATO, EGG, CABBAGE, GARLIC])])
        self.assertEqual([d["recipe_id"] for d in payload["dishes"]], ["R01", "R02"])
        self.assertEqual(payload["knowledge"]["by_ingredient"],
                         {TOMATO: "available", EGG: "available", CABBAGE: "not_found", GARLIC: "not_found"})
        explanation = payload["explanation"]
        self.assertEqual(explanation["status"], "available")
        [item] = explanation["items"]
        self.assertEqual((item["ingredient_id"], item["explanation"]), (TOMATO, "番茄含維生素。"))
        self.assertEqual(item["citations"][0]["source_url"], "https://example.invalid/k/K1")
        self.assertEqual([d["reason"] for d in explanation["dropped"]],
                         ["CITATION_INGREDIENT_MISMATCH", "INGREDIENT_NOT_IN_SNAPSHOT", "CITATION_NOT_IN_SNAPSHOT"])
        # 說明只拿到實際取得的片段
        sent = json.loads(responder.calls[0]["input_text"])
        self.assertEqual([k["knowledge_id"] for k in sent["knowledge"]], ["K1", "K2"])
        evidence = payload["evidence"]
        self.assertEqual((evidence["knowledge_index_version"], evidence["embedding_model"], evidence["explanation_model"]),
                         ("idx-1", "text-embedding-3-small", MODEL))
        # 向量化與說明各一筆已結算的預留
        self.assertEqual([(r.purpose, r.status) for r in self.reservations()],
                         [("knowledge_query_embedding", "settled"), ("knowledge_explanation", "settled")])

    def test_missing_knowledge_is_omitted_without_explanation_call(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder, knowledge=FakeKnowledgeClient([]), embedder=self.embedder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.payload["explanation"], {"status": "not_found", "items": [], "dropped": []})
        self.assertEqual(responder.calls, [])
        self.assertTrue(row.payload["calculation"]["adoptable"])  # 缺知識不阻擋採用

    def test_knowledge_service_failure_is_reported_separately_and_menu_still_ready(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder, knowledge=FakeKnowledgeClient(error="UNREACHABLE"), embedder=self.embedder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        explanation = row.payload["explanation"]
        self.assertEqual((explanation["status"], explanation["code"], explanation["reason"]),
                         ("unavailable", "KNOWLEDGE_UNAVAILABLE", "UNREACHABLE"))
        self.assertEqual(row.payload["knowledge"]["by_ingredient"], {})  # 不偽裝成零筆
        self.assertEqual(responder.calls, [])

    def test_explanation_refusal_hides_explanation_but_keeps_menu(self):
        from app.services.llm_client import StructuredResponse
        self.create_request(dish_count=2)
        job_id = self.submit()
        self.run_job(FakeResponder(StructuredResponse("refused", refusal="no")),
                     knowledge=FakeKnowledgeClient(self.records), embedder=self.embedder)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual((row.payload["explanation"]["status"], row.payload["explanation"]["items"]),
                         ("unavailable", []))


class ResultVersionTests(GenerationTestCase):
    def test_each_generation_is_a_new_version_bound_to_its_request_version(self):
        self.create_request(dish_count=2)
        first_job = self.submit()
        # 同一冪等鍵重送：同一工作，不會多一筆結果
        self.assertEqual(self.submit(), first_job)
        self.run_job()
        updated = self.client.put(f"/meal-requests/{self.request['id']}",
                                  json={"version": 1, "draft": self.draft(dish_count=1)}).json()
        self.request = updated
        second_job = self.submit(key="key-00000002")
        self.run_job(calculator=self.calculator_for(Decimal(100)))  # 一道菜 100 g/n
        rows = self.results()
        self.assertEqual([(r.result_version, r.request_version, str(r.job_id)) for r in rows],
                         [(1, 1, first_job), (2, 2, second_job)])
        self.assertEqual([len(r.payload["dishes"]) for r in rows], [2, 1])
        job = self.job(second_job)
        self.assertEqual(job["result_version"], 2)
        from app.db.models import BackgroundJob
        with self.factory() as db:
            summary = db.get(BackgroundJob, rows[1].job_id).result
        self.assertEqual(summary, {"generation_result_id": str(rows[1].id), "result_version": 2,
                                   "mode": "database_menu", "mode_label": "資料庫菜單", "fallback_reason": None,
                                   "request_status": "awaiting_decision"})  # T15：營養通過、缺價待使用者決定

    def test_result_is_not_written_when_the_lease_was_lost(self):
        from app.services import jobs
        from app.services.generation import make_generation_handler
        self.create_request(dish_count=2)
        job_id = self.submit()
        job = jobs.claim(self.factory, "worker-a")
        from app.jobs.worker import JobContext
        result = make_generation_handler(responder=None, knowledge_client=None, embedder=None,
                                         calculator=self.calculator_for())(JobContext(self.factory, job, "worker-a"))
        from app.services.generation import persist_result
        self.assertFalse(jobs.complete(self.factory, job.id, "someone-else", result, persist=persist_result))
        self.assertEqual(self.results(), [])
        self.assertTrue(jobs.complete(self.factory, job.id, "worker-a", result, persist=persist_result))
        self.assertFalse(jobs.complete(self.factory, job.id, "worker-a", result, persist=persist_result))
        self.assertEqual(len(self.results()), 1)
        self.assertEqual(self.job(job_id)["status"], "succeeded")


class RetrievalOutcomeTests(GenerationTestCase):
    def test_database_failure_is_retried_and_never_falls_back_to_ai(self):
        from sqlalchemy.exc import OperationalError
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        with patch("app.services.recipe_retrieval._load_recipes", side_effect=OperationalError("x", {}, None)):
            self.run_job(responder)
            job = self.job(job_id)
            self.assertEqual((job["status"], job["error"]["code"]), ("queued", "RECIPE_DATABASE_UNAVAILABLE"))
            self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "RECIPE_DATABASE_UNAVAILABLE"))
        self.assertEqual(responder.calls, [])
        self.assertEqual(self.results(), [])

    def test_ambiguous_specified_dish_is_reported_not_guessed(self):
        self.create_request(dish_count=1, specified_dishes=["炒青菜"])
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "DISH_IDENTITY_UNRESOLVED"))
        self.assertIn("炒青菜", job["error"]["message"])
        self.assertEqual(responder.calls, [])


def per_food_calculator(base_energy, base_protein, factors):
    """每克固定含量，個別食材（food_id）可乘倍數；用來讓特定候選組合超出範圍。"""
    from app.services.meal_calculation import IngredientNutrition

    def calc(rows):
        out = []
        for line, grams in rows:
            f = Decimal(factors.get(line.food_id, 1))
            out.append(IngredientNutrition({"energy_kcal": base_energy * grams * f,
                                            "protein_g": base_protein * grams * f}, {"sample_id": "FAKE"}))
        return out
    return calc


class SpecifiedDishConflictTests(GenerationTestCase):
    """pre-review #1：指定菜含禁忌時回報衝突原因，不轉 AI、不生成同名換料的菜，問卷保留。"""

    def allergens(self):
        return [EGG]

    def test_specified_dish_with_allergen_is_reported_without_ai_substitute(self):
        self.create_request(dish_count=2, specified_dishes=["番茄炒蛋"])
        job_id = self.submit()
        responder = FakeResponder(menu(ai_dish("番茄炒蛋", [(TOMATO, 100)])))
        self.run_job(responder)
        job = self.job(job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "SPECIFIED_DISH_DIETARY_CONFLICT"))
        self.assertIn("番茄炒蛋", job["error"]["message"])
        self.assertIn("雞蛋", job["error"]["message"])
        self.assertEqual(responder.calls, [])
        self.assertEqual(self.results(), [])
        request = self.client.get(f"/meal-requests/{self.request['id']}").json()
        self.assertEqual(request["draft"]["specified_dishes"], ["番茄炒蛋"])  # 問卷草稿保留供修改


class DatabaseCandidateTests(GenerationTestCase):
    """pre-review #2：資料庫模式依序評估候選組合，第一個通過 T12 者採用；全部不可行才自動 AI 回退。"""

    def calculator(self, factors):
        energy, protein = self.reference()
        return per_food_calculator(energy / 200, protein / 200, factors)

    def test_next_combination_is_used_when_the_first_fails_nutrition(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder, calculator=self.calculator({CABBAGE: 5}))  # 含高麗菜的組合熱量過高
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.mode, "database_menu")
        self.assertEqual([d["recipe_id"] for d in row.payload["dishes"]], ["R01", "R03"])
        self.assertTrue(row.payload["calculation"]["adoptable"])
        tried = row.payload["database_candidates"]
        self.assertEqual(tried[0]["recipe_ids"], ["R01", "R02"])
        self.assertIn("NUTRITION_OUT_OF_RANGE", tried[0]["block_codes"])
        self.assertEqual(responder.calls, [])

    def test_all_combinations_infeasible_falls_back_to_ai_with_reason(self):
        self.create_request(dish_count=2)
        job_id = self.submit()
        ai_menu = menu(ai_dish("番茄蛋花湯", [(TOMATO, 50), (EGG, 50)], method="煮"),
                       ai_dish("蒜香高麗菜", [(CABBAGE, 50), (GARLIC, 50)]))
        responder = FakeResponder(ai_menu)
        # 所有食材熱量／蛋白質都是 3 倍 → 每個資料庫組合都超出 ±10%（AI 菜同樣超出，只能查看）
        tripled = {f: 3 for f in (TOMATO, EGG, CABBAGE, GARLIC, BOKCHOY, CARROT, BASIL, SOY)}
        self.run_job(responder, calculator=self.calculator(tripled))
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual((row.mode, row.fallback_reason), ("ai_generated_menu", "DATABASE_MENU_NUTRITION_INFEASIBLE"))
        self.assertEqual(len(responder.calls), 1)
        self.assertGreater(len(row.payload["fallback_details"]["tried"]), 1)
        self.assertFalse(row.payload["calculation"]["adoptable"])  # AI 結果仍可查看

    def test_first_viewable_combination_is_kept_when_none_is_adoptable_but_some_only_miss_data(self):
        from app.services.meal_calculation import IngredientNutrition
        self.create_request(dish_count=2)
        energy, _ = self.reference()

        def missing_protein(rows):
            return [IngredientNutrition({"energy_kcal": energy / 200 * g, "protein_g": None}, {}) for _, g in rows]
        job_id = self.submit()
        responder = FakeResponder()
        self.run_job(responder, calculator=missing_protein)
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.mode, "database_menu")  # 缺資料不是「不可行」：保留資料庫菜單讓使用者決定
        self.assertEqual(responder.calls, [])


class KnowledgeBudgetTests(GenerationTestCase):
    """P7：知識步驟額度不足不讓整個工作失敗；菜單保留，知識標 KNOWLEDGE_UNAVAILABLE（額度不足）。"""

    def test_budget_exhausted_during_knowledge_keeps_the_database_menu(self):
        from sqlalchemy import text
        from knowledge_support import FakeEmbedder
        self.create_request(dish_count=2)
        job_id = self.submit()
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE budget_months SET spent_twd = limit_twd - committed_twd"))
        responder = FakeResponder()
        self.run_job(responder, knowledge=FakeKnowledgeClient([knowledge_record("K1", TOMATO, "合成")]),
                     embedder=FakeEmbedder())
        job = self.job(job_id)
        self.assertEqual(job["status"], "succeeded", job)
        [row] = self.results()
        self.assertEqual(row.mode, "database_menu")
        self.assertEqual((row.payload["knowledge"]["status"], row.payload["knowledge"]["reason"]),
                         ("unavailable", "BUDGET_EXHAUSTED"))
        self.assertEqual((row.payload["explanation"]["code"], row.payload["explanation"]["reason"]),
                         ("KNOWLEDGE_UNAVAILABLE", "BUDGET_EXHAUSTED"))
        self.assertTrue(row.payload["calculation"]["adoptable"])
        self.assertEqual(responder.calls, [])


if __name__ == "__main__":
    unittest.main()
