"""T11：團隊電腦營養知識庫（R12、AC29、AC32）。PostgreSQL＋pgvector，合成知識資料。"""
import unittest

from db_support import requires_db

BOKCHOY, TOMATO, EGG, CABBAGE = "FOOD000585", "FOOD001016", "FOOD001566", "FOOD000867"


@requires_db
class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db, record
        self.store = KnowledgeStore(fresh_knowledge_db())
        self.embedder = FakeEmbedder()
        self.record = record
        for rec in (record("K1", BOKCHOY, "青江菜含鈣（合成）"),
                    record("K2", BOKCHOY, "青江菜含維生素（合成）"),
                    record("K3", TOMATO, "番茄含維生素（合成）"),
                    record("K4", TOMATO, "番茄含纖維（合成，待審）"),
                    record("K5", EGG, "雞蛋含鈣（合成，已退回）")):
            self.store.import_record(rec)
        for kid in ("K1", "K2", "K3"):
            self.approve(kid)
        self.store.review("K5", "v1", reviewer="審核者", approved=False, note="引用不符")

    def approve(self, kid, version="v1"):
        self.store.review(kid, version, reviewer="審核者", approved=True, note="已核對來源原文")

    def build(self, version="idx-1"):
        return self.store.build_index(version, "text-embedding-3-small", lambda texts: self.embedder.embed(texts)[0])

    def search(self, food_ids, query="鈣", limit=3, model="text-embedding-3-small"):
        from knowledge_support import fake_vector
        return self.store.search(food_ids, fake_vector(query), model, limit_per_ingredient=limit)

    def ids(self, result):
        return sorted(r["knowledge_id"] for r in result["records"])

    def test_only_approved_current_knowledge_of_final_ingredients_is_returned(self):
        self.build()
        result = self.search([BOKCHOY, TOMATO, EGG, CABBAGE])
        self.assertEqual(self.ids(result), ["K1", "K2", "K3"])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["by_ingredient"], {BOKCHOY: "available", TOMATO: "available",
                                                   EGG: "not_found", CABBAGE: "not_found"})
        self.assertEqual((result["index_version"], result["embedding_model"]), ("idx-1", "text-embedding-3-small"))
        k1 = next(r for r in result["records"] if r["knowledge_id"] == "K1")
        self.assertEqual((k1["ingredient_id"], k1["excerpt"], k1["source_version"]), (BOKCHOY, "青江菜含鈣（合成）", "v1"))
        self.assertEqual(k1["source_url"], "https://example.invalid/knowledge/K1")
        self.assertEqual(k1["reviewer"], "審核者")

    def test_filter_by_ingredient_comes_before_vector_ranking(self):
        self.build()
        result = self.search([TOMATO], query="鈣")
        # 雞蛋的鈣知識距離最近，但不是最終食材，不得出現
        self.assertEqual(self.ids(result), ["K3"])

    def test_vector_distance_ranks_within_each_ingredient(self):
        self.build()
        self.assertEqual(self.ids(self.search([BOKCHOY], query="鈣", limit=1)), ["K1"])
        self.assertEqual(self.ids(self.search([BOKCHOY], query="維生素", limit=1)), ["K2"])

    def test_no_knowledge_for_any_final_ingredient_is_not_found(self):
        self.build()
        result = self.search([CABBAGE])
        self.assertEqual((result["status"], result["records"]), ("not_found", []))

    def test_new_version_supersedes_old_and_needs_review_and_reindex(self):
        self.build()
        self.store.import_record(self.record("K1", BOKCHOY, "青江菜含鈣（合成第二版）", version="v2"))
        # 新版待審：舊版不再可用，新版也還不能用
        self.assertEqual(self.ids(self.search([BOKCHOY])), ["K2"])
        self.approve("K1", "v2")
        # 已審核但索引版本固定：重建索引前不出現
        self.assertEqual(self.ids(self.search([BOKCHOY])), ["K2"])
        self.build("idx-2")
        result = self.search([BOKCHOY])
        self.assertEqual(result["index_version"], "idx-2")
        k1 = next(r for r in result["records"] if r["knowledge_id"] == "K1")
        self.assertEqual((k1["source_version"], k1["excerpt"]), ("v2", "青江菜含鈣（合成第二版）"))

    def test_index_only_embeds_approved_current_records(self):
        built = self.build()
        self.assertEqual(built["records"], 3)
        embedded = [t for call in self.embedder.calls for t in call]
        self.assertEqual(len(embedded), 3)
        self.assertFalse(any("待審" in t or "已退回" in t for t in embedded))

    def test_reviewed_version_is_immutable_and_versions_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            self.approve("K1")
        with self.assertRaises(ValueError):
            self.store.import_record(self.record("K1", BOKCHOY, "改寫內容"))
        with self.assertRaises(ValueError):
            self.store.review("K9", "v1", reviewer="審核者", approved=True, note="x")

    def test_invalid_records_are_rejected(self):
        bad = [dict(self.record("K8", BOKCHOY, "x"), source_url="ftp://example.invalid"),
               dict(self.record("K8", BOKCHOY, "x"), excerpt="  "),
               dict(self.record("K8", BOKCHOY, "x"), ingredient_id="青江菜"),
               {k: v for k, v in self.record("K8", BOKCHOY, "x").items() if k != "rights"},
               # 決策 #29：營養知識的支持句必須逐字出自原文
               dict(self.record("K8", BOKCHOY, "青江菜含鈣"), evidence_quote="青江菜可治病")]
        for rec in bad:
            with self.subTest(rec=rec), self.assertRaises(ValueError):
                self.store.import_record(rec)
        with self.assertRaises(ValueError):
            self.store.review("K4", "v1", reviewer="", approved=True, note="x")

    def test_search_without_active_index_is_an_error_not_zero_rows(self):
        from knowledge_rag.store import KnowledgeStoreError
        with self.assertRaises(KnowledgeStoreError) as caught:
            self.search([BOKCHOY])
        self.assertEqual(caught.exception.code, "NO_ACTIVE_INDEX")

    def test_query_vector_from_another_embedding_model_is_rejected(self):
        from knowledge_rag.store import KnowledgeStoreError
        self.build()
        with self.assertRaises(KnowledgeStoreError) as caught:
            self.search([BOKCHOY], model="text-embedding-3-large")
        self.assertEqual(caught.exception.code, "EMBEDDING_MODEL_MISMATCH")

    def test_wrong_dimension_query_is_rejected(self):
        self.build()
        with self.assertRaises(ValueError):
            self.store.search([BOKCHOY], [0.1, 0.2], "text-embedding-3-small")

    def test_unreachable_store_raises_store_error(self):
        from knowledge_rag.store import KnowledgeStore, KnowledgeStoreError
        broken = KnowledgeStore("postgresql://nobody:wrong@127.0.0.1:1/none?connect_timeout=2")
        with self.assertRaises(KnowledgeStoreError) as caught:
            broken.search([BOKCHOY], [0.0] * 1536, "text-embedding-3-small")
        self.assertEqual(caught.exception.code, "STORE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
