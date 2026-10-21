"""pre-review #6：團隊電腦經雲端內部 API（Access 服務權杖＋Bearer）處理補抓佇列與建索引向量化，
不需要雲端 DATABASE_URL。驗證失敗一律拒絕；向量化仍經 T10 額度；團隊端錯誤是明確的 CloudError。
"""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from db_support import ROOT, migrate_fresh, requires_db, session_factory, truncate_all
from test_kn_admin import INTERNAL_TOKEN, internal_cloud

FOOD = "FOOD000867"


@requires_db
class InternalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from knowledge_support import FakeEmbedder
        from app.main import app
        truncate_all()
        self.factory = session_factory()
        self.embedder = FakeEmbedder()
        token = patch("app.core.config.settings.internal_api_token", INTERNAL_TOKEN)
        token.start()
        self.addCleanup(token.stop)
        self.addCleanup(app.dependency_overrides.clear)
        self.cloud = internal_cloud(self.factory, self.embedder)

    def raw(self, method, path, headers):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app, base_url="https://cloud-internal.example.invalid").request(method, path, headers=headers)

    def test_requests_without_the_internal_bearer_token_are_rejected(self):
        path = "/internal/knowledge/crawl-requests/summary"
        self.assertEqual(self.raw("GET", path, {}).status_code, 401)
        self.assertEqual(self.raw("GET", path, {"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.raw("GET", path, {"Authorization": f"Bearer {INTERNAL_TOKEN}"}).status_code, 200)
        with patch("app.core.config.settings.internal_api_token", None):  # 未設定 → 整組關閉
            self.assertEqual(self.raw("GET", path, {"Authorization": "Bearer "}).status_code, 503)

    def test_queue_claim_and_complete_round_trip(self):
        from app.services import knowledge_queue
        knowledge_queue.record_missing(self.factory, {"status": "not_found", "by_ingredient": {FOOD: "not_found"}})
        self.assertEqual(self.cloud.pending_count(), 1)
        self.assertEqual([r["food_id"] for r in self.cloud.claim(5)], [FOOD])
        self.assertEqual(self.cloud.claim(5), [])  # 已被認領
        self.assertEqual(self.cloud.complete({FOOD: {"pending": 2, "approved": 0}, "bad id": {}}), 1)
        self.assertEqual(self.cloud.pending_count(), 0)

    def test_embedding_goes_through_spend_control_and_budget_errors_are_explicit(self):
        from knowledge_rag.cloud import CloudError
        embed = self.cloud.index_embedder("idx-int-1", self.embedder.model)
        self.assertEqual(len(embed(["高麗菜含纖維"])), 1)
        with self.assertRaises(CloudError) as ctx:
            self.cloud.index_embedder("idx-int-3", "other-model")(["x"])
        self.assertEqual(ctx.exception.code, "MODEL_MISMATCH")
        from sqlalchemy import text
        with self.factory() as db, db.begin():  # 本月額度用完
            db.execute(text("UPDATE budget_months SET spent_twd = limit_twd - committed_twd - reserved_twd"))
        with self.assertRaises(CloudError) as ctx:
            self.cloud.index_embedder("idx-int-2", self.embedder.model)(["x"])
        self.assertEqual(ctx.exception.code, "SERVICE_BUDGET_EXHAUSTED")

    def classify_records(self):
        return [{"knowledge_id": "hn-1-FOOD000867-1", "source_version": "a1", "ingredient_name": "高麗菜",
                 "excerpt": "高麗菜含膳食纖維，可以幫助腸胃消化。"},
                {"knowledge_id": "hn-2-FOOD000867-1", "source_version": "b1", "ingredient_name": "高麗菜",
                 "excerpt": "高麗菜出現在春捲餡料中，保留清甜與脆度。"}]

    def test_classification_goes_through_spend_control_with_gpt_5_4_mini(self):
        # 決策 #29／#11：分類與萃取在雲端以 gpt-5.4-mini Structured Outputs 執行，經 T10 預留／結算
        from app.core.config import settings
        from app.db.models import UsageReservation
        from sqlalchemy import select
        from test_kn_admin import FakeClassifier
        classifier = FakeClassifier()
        cloud = internal_cloud(self.factory, self.embedder, classifier)
        result = cloud.classify_batch("run-1", 0, self.classify_records())
        self.assertEqual(result["model"], settings.generation_model)
        self.assertEqual([(i["knowledge_id"], i["is_nutrition_knowledge"]) for i in result["items"]],
                         [("hn-1-FOOD000867-1", True), ("hn-2-FOOD000867-1", False)])
        self.assertEqual(result["items"][0]["evidence_quote"], "高麗菜含膳食纖維，可以幫助腸胃消化。")
        [call] = classifier.calls
        self.assertEqual(call["model"], "gpt-5.4-mini-2026-03-17")
        self.assertIn("is_nutrition_knowledge", json.dumps(call["schema"]))
        with self.factory() as db:
            rows = db.scalars(select(UsageReservation)).all()
        self.assertEqual([(r.call_key, r.purpose, r.status) for r in rows],
                         [("knowledge-classify:run-1:0", "knowledge_classification", "settled")])

    def test_classification_failures_are_explicit(self):
        from knowledge_rag.cloud import CloudError
        from app.services.llm_client import StructuredResponse
        from test_kn_admin import FakeClassifier
        cloud = internal_cloud(self.factory, self.embedder, None)
        with self.assertRaises(CloudError) as ctx:
            cloud.classify_batch("run-2", 0, self.classify_records())
        self.assertEqual(ctx.exception.code, "CLASSIFIER_NOT_CONFIGURED")
        # 模型回傳格式錯誤：已計費，但整批不採用（留待下次分類），不是空結果
        cloud = internal_cloud(self.factory, self.embedder,
                               FakeClassifier(fail_with=StructuredResponse("completed", output_text="{")))
        with self.assertRaises(CloudError) as ctx:
            cloud.classify_batch("run-3", 0, self.classify_records())
        self.assertEqual(ctx.exception.code, "CLASSIFICATION_INVALID_OUTPUT")
        from sqlalchemy import text
        with self.factory() as db, db.begin():  # 本月額度用完
            db.execute(text("UPDATE budget_months SET spent_twd = limit_twd - committed_twd - reserved_twd"))
        cloud = internal_cloud(self.factory, self.embedder, FakeClassifier())
        with self.assertRaises(CloudError) as ctx:
            cloud.classify_batch("run-4", 0, self.classify_records())
        self.assertEqual(ctx.exception.code, "SERVICE_BUDGET_EXHAUSTED")

    def test_tunnel_outage_and_access_rejection_are_errors_not_empty_queues(self):
        from knowledge_rag.cloud import CloudClient, CloudError
        for status, code in ((530, "UNREACHABLE"), (403, "AUTH_FAILED"), (302, "AUTH_FAILED")):
            client = CloudClient("https://cloud-internal.example.invalid", "t",
                                 transport=lambda *a, s=status: (s, b"<html>"))
            with self.assertRaises(CloudError) as ctx:
                client.claim(5)
            self.assertEqual(ctx.exception.code, code)


class TeamComputerNeedsNoCloudDatabaseTests(unittest.TestCase):
    def test_admin_status_runs_without_database_url(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "OPENAI_API_KEY")}
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "backend")])
        code = ("from knowledge_rag import admin\n"
                "import sys\n"
                "assert 'app.core.config' not in sys.modules\n"
                "print(admin._parser().parse_args(['crawl', '--queued']).queued)\n")
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()
