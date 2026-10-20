"""G07 出處標示：每則顯示給使用者的知識都帶「來源：健康醫療網」、文章標題、發布日期與原文連結。

鏈路：爬蟲匯入（合成文章）→ 審核 → 建索引 → 團隊電腦服務 → 雲端檢索快照 → T14 說明引用 → T15 結果 API。
"""
import unittest

from db_support import FOODS, migrate_fresh, requires_db, session_factory, truncate_all
from test_kn_healthnews_crawler import AUTH_REF, BOKCHOY, SITE, FakeFetcher, site_pages
from test_t11_knowledge_service import TOKEN, paid_call_for, serve
from test_t14_generation import EGG, TOMATO, FakeKnowledgeClient, FakeResponder, GenerationTestCase, knowledge_record

ATTRIBUTION = "來源：健康醫療網《合成：番茄小知識》（2026-08-01）"


@requires_db
class AttributionThroughServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def test_retrieved_knowledge_carries_source_title_date_and_url(self):
        from app.services.knowledge import HttpKnowledgeClient, retrieve_knowledge
        from knowledge_rag.crawler import HealthnewsCrawler
        from knowledge_rag.healthnews import FoodMatcher
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db
        truncate_all()
        store = KnowledgeStore(fresh_knowledge_db())
        matcher = FoodMatcher([(i, name, aliases, []) for i, (name, aliases) in FOODS.items()])
        HealthnewsCrawler(store, FakeFetcher(site_pages()), matcher, authorization_ref=AUTH_REF).crawl_ids(80003, 80003)
        kid = f"hn-80003-{BOKCHOY}-1"
        store.review(kid, store.get_record(kid)["source_version"], reviewer="審核者", approved=True, note="已核對原文")
        embedder = FakeEmbedder()
        store.build_index("idx-1", embedder.model, lambda texts: embedder.embed(texts)[0])
        server, url = serve(store)
        self.addCleanup(server.shutdown)
        snap = retrieve_knowledge(final_food_ids=[BOKCHOY], query_text="鈣", client=HttpKnowledgeClient(url, TOKEN, 5),
                                  embedder=embedder, paid_call=paid_call_for(session_factory()))
        [rec] = snap["records"]
        self.assertEqual((rec["source_name"], rec["title"], rec["source_url"]),
                         ("健康醫療網", "合成：青江菜補鈣小知識", f"{SITE}/article/80003"))
        self.assertEqual(rec["published_at"][:10], "2026-09-01")
        self.assertEqual(rec["content_provider"], "合成月刊")


class AttributionInExplanationTests(GenerationTestCase):
    def test_explanation_citations_and_result_api_keep_attribution(self):
        from knowledge_support import FakeEmbedder
        tomato = {**knowledge_record("hn-80001-FOOD001016-1", TOMATO, "合成：番茄含有茄紅素。"),
                  "source_url": f"{SITE}/article/80001", "source_name": "健康醫療網", "title": "合成：番茄小知識",
                  "published_at": "2026-08-01T01:00:00+00:00", "content_provider": None}
        self.create_request(dish_count=2)
        self.submit()
        cite = {"knowledge_id": tomato["knowledge_id"], "source_version": "v1", "ingredient_id": TOMATO}
        responder = FakeResponder({"items": [{"ingredient_id": TOMATO, "explanation": "番茄含茄紅素。",
                                              "citations": [cite]}]})
        self.run_job(responder, knowledge=FakeKnowledgeClient([tomato, knowledge_record("K2", EGG, "雞蛋（合成）")]),
                     embedder=FakeEmbedder())
        [row] = self.results()
        [citation] = row.payload["explanation"]["items"][0]["citations"]
        self.assertEqual((citation["source_name"], citation["title"], citation["published_at"],
                          citation["source_url"], citation["attribution"]),
                         ("健康醫療網", "合成：番茄小知識", "2026-08-01T01:00:00+00:00", f"{SITE}/article/80001",
                          ATTRIBUTION))
        api = self.client.get(f"/meal-requests/{self.request['id']}/results/1").json()
        [shown] = api["knowledge"]["items"][0]["citations"]
        self.assertEqual((shown["attribution"], shown["source_url"]), (ATTRIBUTION, f"{SITE}/article/80001"))

    def test_attribution_text_uses_taiwan_date_and_is_absent_only_without_source_name(self):
        from app.services.knowledge import attribution
        self.assertEqual(attribution({"source_name": "健康醫療網", "title": "合成：番茄小知識",
                                      "published_at": "2026-07-31T17:00:00+00:00"}), ATTRIBUTION)
        self.assertEqual(attribution({"source_name": "健康醫療網", "title": "合成：無日期", "published_at": None}),
                         "來源：健康醫療網《合成：無日期》")
        self.assertIsNone(attribution({"source_name": None}))


if __name__ == "__main__":
    unittest.main()
