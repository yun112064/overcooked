"""G07 健康醫療網來源：分類頁／文章頁解析、食材比對、段落切分（純函式，合成 HTML 範本）。"""
import unittest
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "healthnews"
CHANNEL = "f72c8a39-1105-2663-255a-b925ad108bac"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ListingParserTests(unittest.TestCase):
    def test_listing_yields_article_ids_with_titles_and_last_page(self):
        from knowledge_rag.healthnews import parse_listing
        listing = parse_listing(fixture("listing_p1.html"))
        self.assertEqual(listing.articles, [("80003", "合成：青江菜補鈣小知識"), ("80002", "合成：外食族三餐建議"),
                                            ("80001", "合成：番茄與雞蛋的搭配")])
        self.assertEqual(listing.last_page, 3)

    def test_last_page_listing_has_no_further_pages(self):
        from knowledge_rag.healthnews import parse_listing
        self.assertEqual(parse_listing(fixture("listing_p3.html")).last_page, 3)

    def test_channel_page_urls(self):
        from knowledge_rag.healthnews import channel_url
        self.assertEqual(channel_url(CHANNEL, 1), f"https://www.healthnews.com.tw/channel/{CHANNEL}/")
        self.assertEqual(channel_url(CHANNEL, 4), f"https://www.healthnews.com.tw/channel/{CHANNEL}/4")


class ArticleParserTests(unittest.TestCase):
    def test_article_metadata_and_body_paragraphs(self):
        from knowledge_rag.healthnews import parse_article
        art = parse_article(fixture("article_80003.html"), "https://www.healthnews.com.tw/article/80003")
        self.assertEqual(art.article_id, "80003")
        self.assertEqual(art.url, "https://www.healthnews.com.tw/article/80003")
        self.assertEqual(art.title, "合成：青江菜補鈣小知識")
        self.assertEqual(art.published_at, "2026-09-01T10:00:00+08:00")
        self.assertEqual(art.modified_at, "2026-09-02T08:30:00+08:00")
        self.assertEqual(art.content_provider, "合成月刊")
        self.assertEqual(art.paragraphs, [
            "合成段落一：青江菜是常見的深綠色蔬菜，含有鈣質。",
            "◎小標題",
            "合成段落二：蛋白質攝取也很重要，雞蛋與豆腐都是來源；紅蘿蔔則提供維生素。",
            "合成段落三：本段沒有提到任何食材，只談作息&睡眠。",
        ])

    def test_ld_json_fills_missing_open_graph_fields(self):
        from knowledge_rag.healthnews import parse_article
        art = parse_article(fixture("article_ldjson_only.html"), "https://www.healthnews.com.tw/article/80001")
        self.assertEqual((art.article_id, art.title, art.published_at), ("80001", "合成：番茄小知識",
                                                                         "2026-08-01T09:00:00+08:00"))
        self.assertIsNone(art.content_provider)

    def test_page_without_article_content_is_not_an_article(self):
        from knowledge_rag.healthnews import parse_article
        self.assertIsNone(parse_article(fixture("article_empty.html"), "https://www.healthnews.com.tw/article/1"))


class MatchingAndPassageTests(unittest.TestCase):
    ROWS = [
        ("FOOD000585", "青江菜", [], []),
        ("FOOD001566", "雞蛋", ["蛋"], []),
        ("FOOD000678", "胡蘿蔔", ["紅蘿蔔"], []),
        ("FOOD001541", "醬油", ["豆油"], []),
        ("FOOD001542", "醬油膏", [], []),
        ("FOOD000039", "大蒜", ["蒜頭"], []),
        ("FOOD001237", "蒜頭", [], []),
        ("FOOD009999", "豆腐", ["白玉"], ["白玉"]),  # 有歧義的別名不可用
    ]

    def matcher(self, groups=()):
        from knowledge_rag.healthnews import FoodMatcher
        return FoodMatcher(self.ROWS, synonym_groups=groups)

    def test_canonical_names_and_approved_aliases_match(self):
        self.assertEqual(self.matcher().match("紅蘿蔔與青江菜"), {"FOOD000678", "FOOD000585"})

    def test_single_character_aliases_and_nutrient_terms_do_not_match(self):
        # 「蛋」單字別名不可用；「蛋白質」不是雞蛋
        self.assertEqual(self.matcher().match("蛋白質很重要，每天一顆蛋"), set())
        self.assertEqual(self.matcher().match("每天一顆雞蛋"), {"FOOD001566"})

    def test_longest_name_wins(self):
        self.assertEqual(self.matcher().match("少用醬油膏"), {"FOOD001542"})

    def test_ambiguous_aliases_are_ignored(self):
        self.assertEqual(self.matcher().match("白玉"), set())

    def test_confirmed_synonym_groups_share_matches(self):
        m = self.matcher(groups=[{"FOOD000039", "FOOD001237"}])
        self.assertEqual(m.match("多吃大蒜"), {"FOOD000039", "FOOD001237"})

    def test_names_for_food_ids(self):
        self.assertEqual(self.matcher().names_for(["FOOD000678"]), ["胡蘿蔔", "紅蘿蔔"])

    def test_passages_group_short_paragraphs_and_attach_heading(self):
        from knowledge_rag.healthnews import split_passages
        paras = ["◎小標題", "甲" * 100, "乙" * 100, "丙" * 300, "丁" * 900]
        passages = split_passages(paras, max_chars=400)
        self.assertEqual(passages[0], "◎小標題\n" + "甲" * 100 + "\n" + "乙" * 100)
        self.assertEqual(passages[1], "丙" * 300)
        # 超長段落依上限切開，不遺失文字
        self.assertEqual("".join(passages[2:]), "丁" * 900)
        self.assertTrue(all(len(p) <= 400 for p in passages))


if __name__ == "__main__":
    unittest.main()
