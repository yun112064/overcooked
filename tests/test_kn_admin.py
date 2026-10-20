"""G07 知識管理指令（python -m knowledge_rag.admin）：待審清單、檢視、核准／退回、建索引、抓取、狀態。

依賴（知識庫、抓取器、食材比對）以注入取代；佇列與建索引向量化經雲端內部 API（pre-review #6），
測試以 FastAPI TestClient 當傳輸層、伺服器端的向量化以假物件取代：不連網、不呼叫付費 API，
團隊電腦端不需要雲端 DATABASE_URL。
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text

from db_support import FOODS, migrate_fresh, requires_db, session_factory, truncate_all
from test_kn_healthnews_crawler import AUTH_REF, BOKCHOY, CABBAGE, CHANNEL, EGG, SITE, TOMATO, FakeFetcher, site_pages

INTERNAL_TOKEN = "internal-token-for-tests-0123456789"


def internal_cloud(factory, embedder):
    """CloudClient → 真實的雲端內部 API（TestClient），伺服器端以測試資料庫與假向量化器執行。"""
    from fastapi.testclient import TestClient
    from app.api import internal_knowledge
    from app.main import app
    from knowledge_rag.cloud import CloudClient
    app.dependency_overrides[internal_knowledge.get_session_factory] = lambda: factory
    app.dependency_overrides[internal_knowledge.get_index_embedder] = lambda: embedder
    http = TestClient(app, base_url="https://cloud-internal.example.invalid")

    def transport(method, url, headers, body, timeout):
        res = http.request(method, url, headers=headers, content=body)
        return res.status_code, res.content
    return CloudClient("https://cloud-internal.example.invalid", INTERNAL_TOKEN, access_client_id="cid",
                       access_client_secret="csecret", transport=transport)



@requires_db
class KnowledgeAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from knowledge_rag.admin import Deps
        from knowledge_rag.healthnews import FoodMatcher
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db
        truncate_all()
        self.factory = session_factory()
        self.store = KnowledgeStore(fresh_knowledge_db())
        self.fetcher = FakeFetcher(site_pages())
        self.embedder = FakeEmbedder()
        matcher = FoodMatcher([(i, name, aliases, []) for i, (name, aliases) in FOODS.items()])
        token = patch("app.core.config.settings.internal_api_token", INTERNAL_TOKEN)
        token.start()
        self.addCleanup(token.stop)
        from app.main import app
        self.addCleanup(app.dependency_overrides.clear)
        cloud = internal_cloud(self.factory, self.embedder)
        self.deps = Deps(store=lambda: self.store, fetcher=lambda: self.fetcher, matcher=lambda: matcher,
                         cloud=lambda: cloud, authorization_ref=AUTH_REF)

    def run_cli(self, *argv):
        from knowledge_rag.admin import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), deps=self.deps)
        self.last_err = err.getvalue()
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def crawl_one(self):
        code, report = self.run_cli("crawl", "--ids", "80003-80003")
        self.assertEqual(code, 0, self.last_err)
        return report

    def test_crawl_then_list_pending_and_show_with_attribution(self):
        report = self.crawl_one()
        self.assertEqual(report["records_created"], 3)
        code, pending = self.run_cli("list", "--status", "pending")
        self.assertEqual(code, 0)
        kid = f"hn-80003-{BOKCHOY}-1"
        self.assertIn(kid, [r["knowledge_id"] for r in pending])
        code, shown = self.run_cli("show", kid)
        [version] = shown["versions"]
        self.assertEqual((version["status"], version["source_name"], version["title"], version["source_url"],
                          version["authorization_ref"]),
                         ("pending", "健康醫療網", "合成：青江菜補鈣小知識", f"{SITE}/article/80003", AUTH_REF))

    def test_approve_and_reject_need_reviewer_and_note(self):
        self.crawl_one()
        kid = f"hn-80003-{BOKCHOY}-1"
        version = self.store.get_record(kid)["source_version"]
        code, _ = self.run_cli("approve", kid, version, "--reviewer", "審核者")
        self.assertNotEqual(code, 0)
        # 轉載文章（內容授權提供／合成月刊）：沒有明確確認轉載授權不可核准
        code, _ = self.run_cli("approve", kid, version, "--reviewer", "審核者", "--note", "已核對原文與出處")
        self.assertNotEqual(code, 0)
        self.assertIn("--confirm-third-party-license", self.last_err)
        code, res = self.run_cli("approve", kid, version, "--reviewer", "審核者", "--note", "已核對原文與出處",
                                 "--confirm-third-party-license")
        self.assertEqual((code, res["status"]), (0, "approved"))
        code, shown = self.run_cli("show", kid)
        self.assertEqual(shown["versions"][0]["license_status"], "third_party_confirmed")
        code, _ = self.run_cli("reject", kid, version, "--reviewer", "審核者", "--note", "改判")
        self.assertNotEqual(code, 0)  # 已審核不可再改
        other = f"hn-80003-FOOD001566-1"
        code, res = self.run_cli("reject", other, self.store.get_record(other)["source_version"],
                                 "--reviewer", "審核者", "--note", "只是順帶提到")
        self.assertEqual((code, res["status"]), (0, "rejected"))

    def bulk(self, *extra, app_env="development"):
        with patch.dict("os.environ", {"APP_ENV": app_env} if app_env is not None else {}, clear=False):
            if app_env is None:
                import os
                os.environ.pop("APP_ENV", None)
            return self.run_cli("approve-all", *extra)

    def test_dev_bulk_approve_skips_reprinted_articles(self):
        # 決策 #24：開發環境可整批核准，紀錄 reviewer=dev-bulk＋註記；轉載文章（third_party_unconfirmed）仍排除
        code, _ = self.run_cli("crawl", "--ids", "80001-80003")
        self.assertEqual(code, 0, self.last_err)
        code, res = self.bulk("--dev-bulk", "--reviewer", "dev-bulk", "--note", "開發環境整批核准")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(sorted(res["approved"]), sorted([f"hn-80001-{TOMATO}-1", f"hn-80001-{EGG}-1"]))
        self.assertIn(f"hn-80003-{BOKCHOY}-1", res["skipped_third_party"])
        tomato = self.store.get_record(f"hn-80001-{TOMATO}-1")
        self.assertEqual((tomato["status"], tomato["reviewer"]), ("approved", "dev-bulk"))
        self.assertIn("開發環境整批核准", tomato["review_note"])
        self.assertEqual(self.store.get_record(f"hn-80003-{BOKCHOY}-1")["status"], "pending")

    def test_dev_bulk_approve_refused_outside_development(self):
        self.crawl_one()
        self.run_cli("crawl", "--ids", "80001-80001")
        for env, extra in (("production", ("--dev-bulk",)), (None, ("--dev-bulk",)), ("development", ())):
            code, _ = self.bulk(*extra, "--reviewer", "dev-bulk", "--note", "x", app_env=env)
            self.assertNotEqual(code, 0, (env, extra))
        self.assertEqual(self.run_cli("list", "--status", "approved")[1], [])

    def test_index_build_reserves_spend_and_activates_approved_only(self):
        self.crawl_one()
        kid = f"hn-80003-{BOKCHOY}-1"
        self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="審核者",
                          approved=True, note="已核對", confirm_third_party_license=True)
        code, res = self.run_cli("index", "build", "--version", "idx-hn-1")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual((res["index_version"], res["records"]), ("idx-hn-1", 1))
        from app.db.models import UsageReservation
        with self.factory() as db:
            rows = db.scalars(select(UsageReservation)).all()
        self.assertEqual([(r.call_key, r.purpose, r.status) for r in rows],
                         [("knowledge-index:idx-hn-1:0", "knowledge_index_embedding", "settled")])
        self.assertEqual(self.store.active_index()["index_version"], "idx-hn-1")

    def test_index_build_stops_when_budget_is_exhausted(self):
        from app.services import spend
        self.crawl_one()
        kid = f"hn-80003-{BOKCHOY}-1"
        self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="審核者",
                          approved=True, note="已核對", confirm_third_party_license=True)
        with patch.object(spend.settings, "monthly_budget_twd", Decimal("1400")):
            code, _ = self.run_cli("index", "build", "--version", "idx-hn-2")
        self.assertNotEqual(code, 0)
        self.assertIn("BUDGET", self.last_err.upper())
        self.assertEqual(self.embedder.calls, [])
        self.assertIsNone(self.store.active_index())

    def test_crawl_channel_with_page_limit(self):
        code, report = self.run_cli("crawl", "--channel", CHANNEL, "--max-pages", "1")
        self.assertEqual((code, report["pages"], report["articles_fetched"]), (0, 1, 3))

    def test_crawl_queued_consumes_cloud_requests_and_marks_them(self):
        from app.services import knowledge_queue
        knowledge_queue.record_missing(self.factory, {"status": "not_found",
                                                      "by_ingredient": {CABBAGE: "not_found", TOMATO: "not_found"}})
        code, report = self.run_cli("crawl", "--queued", "--max-pages", "3")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(report["by_food"][CABBAGE], {"pending": 1, "approved": 0})
        self.assertEqual(knowledge_queue.pending(self.factory), [])
        with self.factory() as db:
            outcome = db.execute(text("SELECT last_outcome FROM knowledge_crawl_requests WHERE food_id = :i"),
                                 {"i": CABBAGE}).scalar_one()
        self.assertEqual(outcome, {"pending": 1, "approved": 0})

    def test_crawl_requires_authorization_reference(self):
        self.deps.authorization_ref = None
        code, _ = self.run_cli("crawl", "--ids", "80003-80003")
        self.assertNotEqual(code, 0)
        self.assertIn("authorization", self.last_err.lower())

    def test_status_summarises_review_queue_index_and_crawl(self):
        self.crawl_one()
        code, status = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(status["records"], {"pending": 3, "approved": 0, "rejected": 0})
        self.assertEqual(status["articles"], {"matched": 1})
        self.assertIsNone(status["active_index"])
        self.assertEqual(status["crawl_queue_pending"], 0)


if __name__ == "__main__":
    unittest.main()
