"""G07 健康醫療網爬蟲 → 團隊電腦知識庫（PENDING、來源欄位、冪等重抓、版本）。假抓取器，不連網。"""
import unittest
from pathlib import Path

from db_support import FOODS, requires_db

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "healthnews"
CHANNEL = "f72c8a39-1105-2663-255a-b925ad108bac"
SITE = "https://www.healthnews.com.tw"
AUTH_REF = "docs/authorizations/healthnews-permission.pdf"
BOKCHOY, EGG, CARROT, CABBAGE, TOMATO = "FOOD000585", "FOOD001566", "FOOD000678", "FOOD000867", "FOOD001016"


def synthetic_article(article_id, title, paragraphs, published="2026-08-01T09:00:00+08:00"):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f'<html><head><meta property="og:title" content="{title} - 健康醫療網"/>'
            f'<meta property="og:url" content="{SITE}/article/{article_id}"/>'
            f'<meta property="article:published_time" content="{published}"></head>'
            f'<body><div id="article-content">{body}</div></body></html>')


class FakeFetcher:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self.pages.get(url)


def site_pages():
    read = lambda n: (FIXTURES / n).read_text(encoding="utf-8")
    return {
        f"{SITE}/channel/{CHANNEL}/": read("listing_p1.html"),
        f"{SITE}/channel/{CHANNEL}/2": read("listing_p2.html"),
        f"{SITE}/channel/{CHANNEL}/3": read("listing_p3.html"),
        f"{SITE}/article/80003": read("article_80003.html"),
        f"{SITE}/article/80002": synthetic_article("80002", "合成：外食族三餐建議", ["合成：規律用餐，細嚼慢嚥。"]),
        f"{SITE}/article/80001": synthetic_article("80001", "合成：番茄與雞蛋的搭配", ["合成：番茄炒雞蛋是家常菜。"]),
        f"{SITE}/article/79990": synthetic_article("79990", "合成：高麗菜的纖維", ["合成：高麗菜含膳食纖維。"]),
        f"{SITE}/article/79980": synthetic_article("79980", "合成：無關主題", ["合成：多運動。"]),
    }


@requires_db
class HealthnewsCrawlerTests(unittest.TestCase):
    def setUp(self):
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db
        self.store = KnowledgeStore(fresh_knowledge_db())
        self.embedder = FakeEmbedder()
        self.fetcher = FakeFetcher(site_pages())

    def crawler(self, **kw):
        from knowledge_rag.crawler import HealthnewsCrawler
        from knowledge_rag.healthnews import FoodMatcher
        matcher = FoodMatcher([(i, name, aliases, []) for i, (name, aliases) in FOODS.items()])
        return HealthnewsCrawler(self.store, self.fetcher, matcher, authorization_ref=AUTH_REF,
                                 max_passage_chars=60, **kw)

    def pending(self):
        return {r["knowledge_id"]: r for r in self.store.list_records(status="pending")}

    def test_channel_crawl_follows_pagination_and_stores_pending_records_with_attribution(self):
        report = self.crawler().crawl_channel(CHANNEL)
        self.assertEqual(report["pages"], 3)
        self.assertEqual(report["articles_fetched"], 5)
        pending = self.pending()
        self.assertEqual(set(pending), {
            f"hn-80003-{BOKCHOY}-1", f"hn-80003-{EGG}-1", f"hn-80003-{CARROT}-1",
            f"hn-80001-{TOMATO}-1", f"hn-80001-{EGG}-1", f"hn-79990-{CABBAGE}-1"})
        rec = self.store.get_record(f"hn-80003-{BOKCHOY}-1")
        self.assertEqual(rec["status"], "pending")
        self.assertEqual(rec["ingredient_id"], BOKCHOY)
        self.assertEqual(rec["source_name"], "健康醫療網")
        self.assertEqual(rec["source_url"], f"{SITE}/article/80003")
        self.assertEqual(rec["title"], "合成：青江菜補鈣小知識")
        self.assertEqual(rec["published_at"].isoformat(), "2026-09-01T02:00:00+00:00")
        self.assertIsNotNone(rec["fetched_at"])
        self.assertEqual(len(rec["content_hash"]), 64)
        self.assertEqual(rec["authorization_ref"], AUTH_REF)
        self.assertEqual(rec["content_provider"], "合成月刊")
        self.assertIn("青江菜", rec["excerpt"])
        self.assertIn(AUTH_REF, rec["rights"])
        # 蛋白質不是雞蛋；雞蛋片段來自真正提到雞蛋的段落
        self.assertIn("雞蛋", self.store.get_record(f"hn-80003-{EGG}-1")["excerpt"])

    def test_pending_crawled_knowledge_is_never_searchable(self):
        from knowledge_support import fake_vector
        self.crawler().crawl_channel(CHANNEL)
        self.store.import_record({"knowledge_id": "K-SYN", "ingredient_id": BOKCHOY, "excerpt": "合成已審核",
                                  "source_url": "https://example.invalid/k", "source_version": "v1",
                                  "rights": "合成", "conditions": "合成"})
        self.store.review("K-SYN", "v1", reviewer="審核者", approved=True, note="ok")
        self.store.build_index("idx-1", "text-embedding-3-small", lambda t: self.embedder.embed(t)[0])
        result = self.store.search([BOKCHOY, EGG, TOMATO], fake_vector("鈣"), "text-embedding-3-small")
        self.assertEqual([r["knowledge_id"] for r in result["records"]], ["K-SYN"])

    def test_recrawl_is_idempotent_and_skips_known_articles(self):
        self.crawler().crawl_channel(CHANNEL)
        before = len(self.pending())
        self.fetcher.calls.clear()
        report = self.crawler().crawl_channel(CHANNEL)
        self.assertEqual(report["records_created"], 0)
        self.assertFalse([u for u in self.fetcher.calls if "/article/" in u])
        refreshed = self.crawler().crawl_channel(CHANNEL, refresh=True)
        self.assertEqual((refreshed["articles_fetched"], refreshed["records_created"]), (5, 0))
        self.assertEqual(refreshed["articles_unchanged"], 5)
        self.assertEqual(len(self.pending()), before)

    def test_changed_article_creates_new_pending_version_and_keeps_approved_version(self):
        self.crawler().crawl_channel(CHANNEL)
        kid = f"hn-79990-{CABBAGE}-1"
        old = self.store.get_record(kid)
        self.store.review(kid, old["source_version"], reviewer="審核者", approved=True, note="已核對原文")
        self.fetcher.pages[f"{SITE}/article/79990"] = synthetic_article(
            "79990", "合成：高麗菜的纖維", ["合成：高麗菜含膳食纖維，另有維生素。"])
        report = self.crawler().crawl_ids(79990, 79990, refresh=True)
        self.assertEqual((report["records_created"], report["superseded_approved"]), (1, [kid]))
        versions = self.store.list_versions(kid)
        self.assertEqual([v["status"] for v in versions], ["approved", "pending"])
        self.assertEqual(versions[0]["excerpt"], old["excerpt"])  # 舊版未被覆寫
        self.assertEqual(versions[0]["reviewer"], "審核者")
        self.assertNotEqual(versions[1]["content_hash"], versions[0]["content_hash"])

    def test_id_range_crawl_tolerates_missing_articles(self):
        report = self.crawler().crawl_ids(80000, 80003)
        self.assertEqual(report["articles_fetched"], 3)
        self.assertEqual(report["articles_missing"], 1)  # 80000 不存在
        self.assertIn(f"hn-80001-{TOMATO}-1", self.pending())

    def test_authorization_reference_is_required(self):
        from knowledge_rag.crawler import HealthnewsCrawler
        from knowledge_rag.healthnews import FoodMatcher
        with self.assertRaises(ValueError):
            HealthnewsCrawler(self.store, self.fetcher, FoodMatcher([]), authorization_ref=" ")

    def test_queued_foods_are_crawled_first_by_listing_title(self):
        report = self.crawler().crawl_for_foods([CABBAGE], channels=[CHANNEL], max_pages=3, max_articles=1)
        fetched = [u for u in self.fetcher.calls if "/article/" in u]
        self.assertEqual(fetched, [f"{SITE}/article/79990"])
        self.assertEqual(report["by_food"][CABBAGE], {"pending": 1, "approved": 0})


if __name__ == "__main__":
    unittest.main()
