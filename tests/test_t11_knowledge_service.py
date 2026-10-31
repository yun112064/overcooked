"""T11：雲端呼叫團隊電腦知識 RAG（R12、AC29、AC32；費用控制 R11）。

真實 HTTP：測試在本機啟動 knowledge_rag/server.py 的處理器，連到測試知識庫；
OpenAI 向量化以假轉接器取代（不呼叫付費 API），額度預留／結算寫入雲端測試庫。
"""
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import httpx
from sqlalchemy import select

from db_support import migrate_fresh, requires_db, session_factory, truncate_all
from app.services.team_link import TeamCredentials

BOKCHOY, TOMATO, EGG, CABBAGE = "FOOD000585", "FOOD001016", "FOOD001566", "FOOD000867"
TOKEN = "test-knowledge-token"


def serve(store, token=TOKEN):
    from knowledge_rag.server import make_handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def paid_call_for(factory, prefix="job-1:1"):
    from app.services import spend

    def paid_call(*, name, **kwargs):
        return spend.paid_call(factory, call_key=f"{prefix}:{name}", **kwargs)
    return paid_call


@requires_db
class KnowledgeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from app.services.knowledge import HttpKnowledgeClient
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db, record
        truncate_all()
        self.factory = session_factory()
        self.store = KnowledgeStore(fresh_knowledge_db())
        for rec in (record("K1", BOKCHOY, "青江菜含鈣（合成）"), record("K2", BOKCHOY, "青江菜含維生素（合成）"),
                    record("K3", TOMATO, "番茄含維生素（合成）"), record("K4", TOMATO, "番茄含纖維（合成，待審）"),
                    record("K5", EGG, "雞蛋含鈣（合成）")):
            self.store.import_record(rec)
        for kid in ("K1", "K2", "K3", "K5"):
            self.store.review(kid, "v1", reviewer="審核者", approved=True, note="已核對來源原文")
        self.embedder = FakeEmbedder()
        self.store.build_index("idx-1", self.embedder.model, lambda texts: self.embedder.embed(texts)[0])
        self.embedder.calls.clear()
        self.server, self.url = serve(self.store)
        self.addCleanup(self.server.shutdown)
        self.client = HttpKnowledgeClient(self.url, TeamCredentials(TOKEN), timeout=5)

    def retrieve(self, food_ids, *, client="default", embedder="default", query="鈣", call_name="embed_query"):
        from app.services.knowledge import retrieve_knowledge
        return retrieve_knowledge(final_food_ids=food_ids, query_text=query, call_name=call_name,
                                  client=self.client if client == "default" else client,
                                  embedder=self.embedder if embedder == "default" else embedder,
                                  paid_call=paid_call_for(self.factory))

    def reservations(self):
        from app.db.models import UsageReservation
        with self.factory() as db:
            return db.scalars(select(UsageReservation).order_by(UsageReservation.created_at)).all()

    # --- 正常檢索與快照 ---

    def test_returns_actual_knowledge_ids_excerpts_sources_and_versions(self):
        snap = self.retrieve([BOKCHOY, TOMATO, CABBAGE])
        self.assertEqual(snap["status"], "available")
        self.assertEqual(sorted(r["knowledge_id"] for r in snap["records"]), ["K1", "K2", "K3"])
        self.assertEqual(snap["by_ingredient"], {BOKCHOY: "available", CABBAGE: "not_found", TOMATO: "available"})
        self.assertEqual((snap["index_version"], snap["embedding_model"]), ("idx-1", "text-embedding-3-small"))
        k3 = next(r for r in snap["records"] if r["knowledge_id"] == "K3")
        self.assertEqual((k3["ingredient_id"], k3["excerpt"], k3["source_version"], k3["source_url"]),
                         (TOMATO, "番茄含維生素（合成）", "v1", "https://example.invalid/knowledge/K3"))
        self.assertEqual(snap["final_food_ids"], [BOKCHOY, CABBAGE, TOMATO])

    def test_query_embedding_is_reserved_and_settled(self):
        from app.services import spend
        self.retrieve([BOKCHOY], query="鈣質")
        self.assertEqual(self.embedder.calls, [["鈣質"]])
        [row] = self.reservations()
        self.assertEqual((row.status, row.model, row.purpose, row.call_key),
                         ("settled", "text-embedding-3-small", "knowledge_query_embedding", "job-1:1:embed_query"))
        self.assertEqual(row.usage, {"input_tokens": 2, "output_tokens": 0})
        self.assertEqual(row.actual_twd, spend.cost_twd("text-embedding-3-small", 2, 0))
        self.assertGreaterEqual(row.reserved_twd, row.actual_twd)

    def test_no_knowledge_is_an_omittable_state(self):
        snap = self.retrieve([CABBAGE])
        self.assertEqual((snap["status"], snap["records"]), ("not_found", []))
        self.assertEqual(snap["by_ingredient"], {CABBAGE: "not_found"})

    def test_empty_final_ingredients_skip_paid_call(self):
        snap = self.retrieve([])
        self.assertEqual(snap["status"], "not_found")
        self.assertEqual(self.reservations(), [])

    def test_changed_recipe_ingredients_are_remapped(self):
        from app.services.knowledge import verify_citations
        before = self.retrieve([BOKCHOY, TOMATO])
        # 同一次嘗試內重新對應要用不同呼叫名稱，避免重複預留
        after = self.retrieve([TOMATO, EGG], query="鈣", call_name="embed_query_remap")
        self.assertEqual(sorted(r["knowledge_id"] for r in after["records"]), ["K3", "K5"])
        # 已移除食材（青江菜）的引用對新快照不再有效
        old_citation = {"knowledge_id": "K1", "source_version": "v1", "ingredient_id": BOKCHOY}
        self.assertIn(old_citation, [{k: r[k] for k in ("knowledge_id", "source_version", "ingredient_id")}
                                     for r in before["records"]])
        self.assertEqual(verify_citations([old_citation], after)["invalid"], [old_citation])

    # --- 引用核對（AC29） ---

    def test_citations_must_match_retrieved_reviewed_knowledge(self):
        from app.services.knowledge import verify_citations
        snap = self.retrieve([BOKCHOY, TOMATO])
        good = {"knowledge_id": "K1", "source_version": "v1", "ingredient_id": BOKCHOY}
        wrong_ingredient = {"knowledge_id": "K1", "source_version": "v1", "ingredient_id": TOMATO}
        unreviewed = {"knowledge_id": "K4", "source_version": "v1", "ingredient_id": TOMATO}
        fabricated = {"knowledge_id": "K99", "source_version": "v1", "ingredient_id": TOMATO}
        wrong_version = {"knowledge_id": "K3", "source_version": "v0", "ingredient_id": TOMATO}
        result = verify_citations([good, wrong_ingredient, unreviewed, fabricated, wrong_version], snap)
        self.assertFalse(result["valid"])
        self.assertEqual(result["citations"], [good])
        self.assertEqual(result["invalid"], [wrong_ingredient, unreviewed, fabricated, wrong_version])
        self.assertTrue(verify_citations([good], snap)["valid"])

    def test_unavailable_snapshot_cannot_back_any_citation(self):
        from app.services.knowledge import verify_citations
        snap = self.retrieve([BOKCHOY], client=None)
        self.assertFalse(verify_citations([{"knowledge_id": "K1", "source_version": "v1",
                                            "ingredient_id": BOKCHOY}], snap)["valid"])

    # --- 服務故障與正常零筆分開 ---

    def test_unconfigured_service_is_unavailable_without_spending(self):
        snap = self.retrieve([BOKCHOY], client=None)
        self.assertEqual((snap["status"], snap["reason"], snap["records"]), ("unavailable", "NOT_CONFIGURED", []))
        self.assertEqual(self.reservations(), [])
        snap = self.retrieve([BOKCHOY], embedder=None)
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "EMBEDDING_NOT_CONFIGURED"))

    def test_wrong_token_is_unavailable(self):
        from app.services.knowledge import HttpKnowledgeClient
        snap = self.retrieve([BOKCHOY], client=HttpKnowledgeClient(self.url, TeamCredentials("wrong-token"), timeout=5))
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "AUTH_FAILED"))

    def test_unreachable_service_is_unavailable(self):
        from app.services.knowledge import HttpKnowledgeClient
        self.server.shutdown()
        self.server.server_close()
        snap = self.retrieve([BOKCHOY], client=HttpKnowledgeClient(self.url, TeamCredentials(TOKEN), timeout=8))
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "UNREACHABLE"))

    def test_broken_knowledge_database_is_unavailable_not_zero_rows(self):
        from app.services.knowledge import HttpKnowledgeClient
        from knowledge_rag.store import KnowledgeStore
        server, url = serve(KnowledgeStore("postgresql://nobody:wrong@127.0.0.1:1/none?connect_timeout=2"))
        self.addCleanup(server.shutdown)
        snap = self.retrieve([BOKCHOY], client=HttpKnowledgeClient(url, TeamCredentials(TOKEN), timeout=10))
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "SERVICE_ERROR"))

    def test_missing_active_index_is_unavailable(self):
        from knowledge_support import fresh_knowledge_db
        fresh_knowledge_db()
        snap = self.retrieve([BOKCHOY])
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "SERVICE_ERROR"))

    def test_embedding_model_mismatch_is_unavailable(self):
        class OtherModel(type(self.embedder)):
            model = "text-embedding-3-large"
        with patch("app.services.spend.cost_twd", return_value=Decimal("0.0001")):
            snap = self.retrieve([BOKCHOY], embedder=OtherModel())
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "MODEL_MISMATCH"))

    def test_records_for_other_ingredients_or_unreviewed_are_rejected_as_invalid(self):
        from app.services.knowledge import HttpKnowledgeClient

        class Lying:
            def search(self, food_ids, query_embedding, embedding_model, *, limit_per_ingredient):
                body = self.store.search(food_ids, query_embedding, embedding_model,
                                         limit_per_ingredient=limit_per_ingredient)
                body["records"][0]["ingredient_id"] = EGG
                return body
        lying = Lying()
        lying.store = self.store
        with patch.object(HttpKnowledgeClient, "_post", side_effect=lambda payload: lying.search(
                payload["food_ids"], payload["query_embedding"], payload["embedding_model"],
                limit_per_ingredient=payload["limit_per_ingredient"])):
            snap = self.retrieve([BOKCHOY])
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "INVALID_RESPONSE"))

    def test_not_billed_embedding_failure_is_unavailable_and_released(self):
        from app.services import spend

        class Down(type(self.embedder)):
            def embed(self, texts):
                raise spend.CallNotBilled("connect failed")
        snap = self.retrieve([BOKCHOY], embedder=Down())
        self.assertEqual((snap["status"], snap["reason"]), ("unavailable", "EMBEDDING_UNAVAILABLE"))
        self.assertEqual([r.status for r in self.reservations()], ["released"])

    def test_budget_exhaustion_stops_before_any_paid_call(self):
        from app.services import spend
        with patch.object(spend.settings, "monthly_budget_twd", Decimal("1400")):
            with self.assertRaises(spend.BudgetExhausted):
                self.retrieve([BOKCHOY])
        self.assertEqual(self.embedder.calls, [])

    def test_health_endpoint_reports_pinned_index(self):
        res = httpx.get(f"{self.url}/v1/health", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(res.json(), {"status": "ok", "index_version": "idx-1",
                                      "embedding_model": "text-embedding-3-small"})
        self.assertEqual(httpx.get(f"{self.url}/v1/health").status_code, 401)

    # --- 建索引的向量化同樣受額度控制 ---

    def test_index_build_embeddings_are_reserved_and_settled(self):
        from app.services.knowledge import embed_index_batch
        self.store.import_record(__import__("knowledge_support").record("K6", CABBAGE, "高麗菜含纖維（合成）"))
        self.store.review("K6", "v1", reviewer="審核者", approved=True, note="已核對")
        batches = iter(range(100))
        built = self.store.build_index(
            "idx-2", self.embedder.model,
            lambda texts: embed_index_batch(self.factory, self.embedder, "idx-2", next(batches), texts), batch_size=2)
        self.assertEqual(built["records"], 5)
        rows = self.reservations()
        self.assertEqual([r.call_key for r in rows], ["knowledge-index:idx-2:0", "knowledge-index:idx-2:1",
                                                      "knowledge-index:idx-2:2"])
        self.assertTrue(all(r.status == "settled" and r.purpose == "knowledge_index_embedding" for r in rows))
        snap = self.retrieve([CABBAGE], query="纖維")
        self.assertEqual(([r["knowledge_id"] for r in snap["records"]], snap["index_version"]), (["K6"], "idx-2"))


class OpenAIEmbedderTests(unittest.TestCase):
    """OpenAI 向量化轉接器：以替換 httpx.post 測試，不呼叫真實 API。"""

    def embedder(self):
        from app.services.llm_client import OpenAIEmbedder
        return OpenAIEmbedder(api_key="sk-test-not-real", model="text-embedding-3-small",
                              base_url="https://api.openai.test/v1", timeout=5)

    def test_parses_vectors_in_input_order_and_usage(self):
        body = {"data": [{"index": 1, "embedding": [0.2] * 1536}, {"index": 0, "embedding": [0.1] * 1536}],
                "model": "text-embedding-3-small", "usage": {"prompt_tokens": 7, "total_tokens": 7}}
        with patch("httpx.post", return_value=httpx.Response(200, json=body)) as post:
            vectors, usage = self.embedder().embed(["甲", "乙"])
        self.assertEqual((vectors[0][0], vectors[1][0]), (0.1, 0.2))
        self.assertEqual(usage, {"input_tokens": 7, "output_tokens": 0})
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.openai.test/v1/embeddings")
        self.assertEqual(kwargs["json"], {"model": "text-embedding-3-small", "input": ["甲", "乙"]})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test-not-real")

    def test_connection_failure_is_not_billed(self):
        from app.services import spend
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with self.assertRaises(spend.CallNotBilled):
                self.embedder().embed(["甲"])

    def test_rejected_request_is_not_billed_but_server_error_is_uncertain(self):
        from app.services import spend
        with patch("httpx.post", return_value=httpx.Response(429, json={"error": {}})):
            with self.assertRaises(spend.CallNotBilled):
                self.embedder().embed(["甲"])
        with patch("httpx.post", return_value=httpx.Response(500, json={"error": {}})):
            with self.assertRaises(Exception) as caught:
                self.embedder().embed(["甲"])
        self.assertNotIsInstance(caught.exception, spend.CallNotBilled)

    def test_malformed_response_is_uncertain(self):
        from app.services import spend
        with patch("httpx.post", return_value=httpx.Response(200, json={"data": []})):
            with self.assertRaises(Exception) as caught:
                self.embedder().embed(["甲"])
        self.assertNotIsInstance(caught.exception, spend.CallNotBilled)

    def test_unset_api_key_means_no_embedder(self):
        from app.core.config import settings
        from app.services.llm_client import default_embedder
        with patch.object(settings, "openai_api_key", None):
            self.assertIsNone(default_embedder())
        with patch.object(settings, "openai_api_key", "sk-test-not-real"):
            self.assertEqual(default_embedder().model, "text-embedding-3-small")

    def test_embedding_model_is_priced(self):
        from app.services import spend
        self.assertEqual(spend.cost_twd("text-embedding-3-small", 1_000_000, 0), Decimal("0.6600"))


if __name__ == "__main__":
    unittest.main()
