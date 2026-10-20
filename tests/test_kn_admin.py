"""G07 知識管理指令（python -m knowledge_rag.admin）：待審清單、檢視、核准／退回、建索引、抓取、狀態。

依賴（知識庫、抓取器、食材比對、雲端資料庫、向量化）以注入取代；不連網、不呼叫付費 API。
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text

from db_support import FOODS, migrate_fresh, requires_db, session_factory, truncate_all
from test_kn_healthnews_crawler import AUTH_REF, BOKCHOY, CABBAGE, CHANNEL, SITE, TOMATO, FakeFetcher, site_pages


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
        self.deps = Deps(store=lambda: self.store, fetcher=lambda: self.fetcher, matcher=lambda: matcher,
                         cloud_factory=lambda: self.factory, embedder=lambda: self.embedder,
                         authorization_ref=AUTH_REF)

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
        code, res = self.run_cli("approve", kid, version, "--reviewer", "審核者", "--note", "已核對原文與出處")
        self.assertEqual((code, res["status"]), (0, "approved"))
        code, _ = self.run_cli("reject", kid, version, "--reviewer", "審核者", "--note", "改判")
        self.assertNotEqual(code, 0)  # 已審核不可再改
        other = f"hn-80003-FOOD001566-1"
        code, res = self.run_cli("reject", other, self.store.get_record(other)["source_version"],
                                 "--reviewer", "審核者", "--note", "只是順帶提到")
        self.assertEqual((code, res["status"]), (0, "rejected"))

    def test_index_build_reserves_spend_and_activates_approved_only(self):
        self.crawl_one()
        kid = f"hn-80003-{BOKCHOY}-1"
        self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="審核者",
                          approved=True, note="已核對")
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
                          approved=True, note="已核對")
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
