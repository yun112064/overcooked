"""Shared synthetic fixtures for T11 knowledge attribution and crawl-queue tests.

Extracted from origin/1102_V1 tests/test_t14_generation.py lines 1–178.
工作流程經 T10 工作程序（run_once）與測試資料庫執行；付費模型、向量化與知識服務以假物件
取代（不呼叫真實付費 API），額度預留／結算仍寫入資料庫。食譜與知識皆為合成資料。
營養以假 calculator 計算（每克固定含量），讓預期值可由人工推算；TFDA 管線另由 T12 測試。
"""
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select

from db_support import make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all
from test_t11_recipe_retrieval import BASIL, BOKCHOY, CABBAGE, CARROT, EGG, GARLIC, SOY, TOMATO, seed_recipes, RECIPES
from test_t11_recipe_retrieval import fix_tie_break_order

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
    """AI 菜（決策 #49）：烹調方式為列舉值清單（可多選），食材一律填生重、不再由模型標生熟。"""
    methods = [method] if isinstance(method, str) else list(method)
    return {"dish_name": name, "aliases": list(aliases), "cooking_methods": methods, "steps": list(steps),
            "ingredients": [{"name": FOOD_NAMES.get(food, food), "food_id": food if food in FOOD_NAMES else None,
                             "grams_per_n": grams} for food, grams in ingredients]}


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


def knowledge_record(kid, food_id, excerpt, nutrition_fact=..., evidence_quote=...):
    """檢索到的審核知識；預設整段即為營養知識與支持句（決策 #29），可傳 None 模擬未分類／非營養段落。"""
    return {"knowledge_id": kid, "source_version": "v1", "ingredient_id": food_id, "excerpt": excerpt,
            "source_url": f"https://example.invalid/k/{kid}", "rights": "合成測試資料",
            "conditions": "一般成人；合成情境", "reviewer": "審核者", "reviewed_at": "2026-10-01T00:00:00Z",
            "distance": 0.1, "nutrition_fact": excerpt if nutrition_fact is ... else nutrition_fact,
            "evidence_quote": excerpt if evidence_quote is ... else evidence_quote}


@requires_db
class GenerationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        fix_tie_break_order(self)  # 合成食譜的期望值依 recipe_id 平手順序寫定（輪替另由 T11 測試）
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
            from app.services import jobs
            from sqlalchemy import text
            # Use the worker's clock; database time may be slightly ahead on Docker.
            ready_at = jobs._now() - timedelta(seconds=1)
            with self.factory() as db, db.begin():
                db.execute(text("UPDATE background_jobs SET available_at = :ready_at WHERE status = 'queued'"),
                           {"ready_at": ready_at})
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
