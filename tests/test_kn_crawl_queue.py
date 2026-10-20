"""G07「生成後查詢所用食材」：最終食材缺審核知識 → 雲端記錄待補抓食材，團隊電腦爬蟲優先處理。

記錄只含 FOOD 編號與次數（不含家庭或使用者資料）；記錄失敗絕不影響生成；未審核內容不會因此顯示。
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from test_t14_generation import (CABBAGE, EGG, GARLIC, TOMATO, FakeKnowledgeClient, FakeResponder,
                                 GenerationTestCase, knowledge_record)


class CrawlQueueAfterGenerationTests(GenerationTestCase):
    """資料庫菜單 R01 番茄炒蛋＋R02 清炒高麗菜 → 最終食材：番茄、雞蛋、高麗菜、大蒜。"""

    def setUp(self):
        super().setUp()
        from knowledge_support import FakeEmbedder
        self.embedder = FakeEmbedder()
        self.records = [knowledge_record("K1", TOMATO, "番茄含維生素（合成）"),
                        knowledge_record("K2", EGG, "雞蛋含蛋白質（合成）")]

    def queue(self):
        with self.factory() as db:
            return {r.food_id: r for r in db.execute(text("SELECT * FROM knowledge_crawl_requests"))}

    def generate(self, client):
        self.create_request(dish_count=2)
        job_id = self.submit()
        self.run_job(FakeResponder({"items": []}), knowledge=client, embedder=self.embedder)
        return self.job(job_id)

    def test_final_ingredients_without_approved_knowledge_are_queued(self):
        job = self.generate(FakeKnowledgeClient(self.records))
        self.assertEqual(job["status"], "succeeded")
        queue = self.queue()
        self.assertEqual(set(queue), {CABBAGE, GARLIC})
        self.assertEqual({r.status for r in queue.values()}, {"pending"})
        self.assertEqual(queue[CABBAGE].request_count, 1)
        with self.factory() as db:
            columns = set(db.execute(text("SELECT * FROM knowledge_crawl_requests LIMIT 0")).keys())
        self.assertFalse(columns & {"household_id", "user_id", "meal_request_id", "job_id"})

    def test_unavailable_knowledge_service_queues_nothing(self):
        self.generate(FakeKnowledgeClient(error="UNREACHABLE"))
        self.assertEqual(self.queue(), {})

    def test_queue_failure_never_fails_generation(self):
        with patch("app.services.knowledge_queue._upsert", side_effect=RuntimeError("db down")):
            job = self.generate(FakeKnowledgeClient(self.records))
        self.assertEqual(job["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.payload["knowledge"]["by_ingredient"][CABBAGE], "not_found")


class CrawlQueueServiceTests(GenerationTestCase):
    T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def snap(self, *missing, found=()):
        by = {i: "not_found" for i in missing} | {i: "available" for i in found}
        return {"status": "available" if found else "not_found", "by_ingredient": by}

    def test_repeat_requests_count_and_pending_orders_by_demand(self):
        from app.services import knowledge_queue as q
        q.record_missing(self.factory, self.snap(CABBAGE), now=self.T0)
        q.record_missing(self.factory, self.snap(CABBAGE, GARLIC), now=self.T0 + timedelta(hours=1))
        pending = q.pending(self.factory)
        self.assertEqual([(p["food_id"], p["request_count"]) for p in pending], [(CABBAGE, 2), (GARLIC, 1)])

    def test_crawled_requests_are_not_reopened_until_recrawl_interval(self):
        from app.services import knowledge_queue as q
        q.record_missing(self.factory, self.snap(CABBAGE), now=self.T0)
        q.mark_crawled(self.factory, {CABBAGE: {"pending": 1, "approved": 0}}, now=self.T0)
        q.record_missing(self.factory, self.snap(CABBAGE), now=self.T0 + timedelta(days=1))
        self.assertEqual(q.pending(self.factory), [])
        q.record_missing(self.factory, self.snap(CABBAGE), now=self.T0 + q.RECRAWL_AFTER + timedelta(days=1))
        self.assertEqual([p["food_id"] for p in q.pending(self.factory)], [CABBAGE])

    def test_only_valid_food_ids_from_usable_snapshots_are_recorded(self):
        from app.services import knowledge_queue as q
        self.assertEqual(q.record_missing(self.factory, {"status": "unavailable", "by_ingredient": {}}), [])
        self.assertEqual(q.record_missing(self.factory, {"status": "not_found",
                                                         "by_ingredient": {"bad id": "not_found"}}), [])
        self.assertEqual(q.pending(self.factory), [])


if __name__ == "__main__":
    unittest.main()
