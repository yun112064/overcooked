"""R14 飲食客服語料爬取（步驟 1）：兩來源解析、列表探索、接續與資料目錄限制。合成 HTML、假 fetcher，不連網。"""
import json

import pytest

from assistant_rag import config, crawl
from assistant_rag.htmltext import blocks, to_sections
from assistant_rag.sources import healthnews, helloyishi
from knowledge_rag.fetcher import FetchError

HN_ARTICLE = """<html><head>
<meta property="og:title" content="地瓜好處多 營養師教你這樣吃 - 健康醫療網"/>
<meta property="og:url" content="https://www.healthnews.com.tw/article/123"/>
<meta property="article:published_time" content="2026-10-01T08:00:00+08:00">
<meta name="description" content="地瓜富含膳食纖維。">
<meta name="keywords" content="地瓜,膳食纖維,營養師" />
<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","author":{"name":"記者王小明"}}]}</script>
</head><body><div id="article-content" class="x">
<p>地瓜富含膳食纖維，有助腸道蠕動。</p>
<h2><span><strong>地瓜的營養</strong></span></h2>
<p>地瓜含有維生素A。</p>
<p style="text-align:center"><img src="a.jpg"/></p>
<p><strong>▲營養師示範地瓜料理。</strong></p>
<div class="inner"><p>建議連皮一起吃。</p></div>
<p>（內容授權提供／臺中榮總）</p>
</div>
<div class="keyword-link"><a href="#">加入【健康醫療網】</a></div>
</body></html>"""

HY_CONTENT = ("<p>地瓜是常見的根莖類。</p><p><h2 id='x'>地瓜的好處</h2></p>"
              "<h3>好處1. 補充纖維</h3><ul><li>膳食纖維</li><li>鉀</li></ul>"
              "<p>（延伸閱讀：地瓜葉怎麼吃）</p><p>[embed-health-tool-bmi]</p>")


def hy_page(**overrides) -> str:
    article = {"ID": 42, "post_title": "地瓜功效一次看", "post_date": "2026-01-09 09:18:34",
               "post_modified": "2026-01-14 09:31:12", "permalink": "/healthy-eating/nutrition/sweet-potato/",
               "post_content": HY_CONTENT, "sources": "<p>地瓜（食藥署）https://example.org</p>",
               "primary_category": {"name": "營養知識"}, "categories": [{"name": "營養知識"}],
               "reviewed": {"author": {"display_name": "張如青"}, "expert": {"display_name": "王醫師"},
                            "fact_check": {"display_name": "Hello 醫師"}},
               "meta_tag": '<meta name="description" content="地瓜的營養&amp;好處">',
               "is_sponsored": False, "sponsor": None, "faqs": [{"question": "地瓜可以天天吃嗎？",
                                                                 "answer": "<p>可以適量食用。</p>"}]}
    article.update(overrides)
    data = {"props": {"pageProps": {"articlesV2": [article]}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></html>'


class FakeFetcher:
    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        value = self.pages.get(url)
        if isinstance(value, Exception):
            raise value
        return value


def test_healthnews_article_sections_provider_and_metadata():
    r = healthnews.parse(HN_ARTICLE, "https://www.healthnews.com.tw/article/123")
    assert r["title"] == "地瓜好處多 營養師教你這樣吃"
    assert r["article_id"] == "123" and r["author"] == "記者王小明"
    assert r["keywords"] == ["地瓜", "膳食纖維", "營養師"]
    assert r["content_provider"] == "臺中榮總"
    assert [s["heading"] for s in r["sections"]] == [None, "地瓜的營養"]
    assert r["sections"][1]["paragraphs"] == ["地瓜含有維生素A。", "建議連皮一起吃。"]  # 圖說、供稿者行、站外連結不算內文
    assert "加入" not in r["content"] and r["char_count"] == len(r["content"])
    assert len(r["content_hash"]) == 64


def test_healthnews_title_suffixes_removed():
    assert healthnews.clean_title("地瓜好處多 - 健康醫療網 - 健康養生新聞資訊網路媒體") == "地瓜好處多"
    assert healthnews.clean_title("地瓜好處多｜健康醫療網") == "地瓜好處多"
    assert healthnews.clean_title("健康醫療網專訪") == "健康醫療網專訪"


def test_healthnews_without_body_is_none():
    assert healthnews.parse("<html><meta property=\"og:title\" content=\"x\"/></html>", "https://x/article/1") is None


def test_helloyishi_heading_inside_paragraph_reviewers_and_faq():
    r = helloyishi.parse(hy_page(), "https://helloyishi.com.tw/healthy-eating/nutrition/sweet-potato/")
    assert r["url"] == "https://helloyishi.com.tw/healthy-eating/nutrition/sweet-potato/"
    assert r["published_at"] == "2026-01-09T09:18:34+08:00"
    assert r["reviewers"] == {"撰文": "張如青", "醫學審稿": "王醫師", "資料查核": "Hello 醫師"}
    assert r["subcategory"] == "營養知識" and r["description"] == "地瓜的營養&好處"
    paths = [s["heading_path"] for s in r["sections"]]
    assert paths == [[], ["地瓜的好處", "好處1. 補充纖維"], ["常見問題"]]
    assert r["sections"][1]["paragraphs"] == ["膳食纖維", "鉀"]  # 延伸閱讀與短代碼已移除
    assert r["sections"][2]["paragraphs"] == ["地瓜可以天天吃嗎？ 可以適量食用。"]
    assert r["references"] == ["地瓜（食藥署）https://example.org"]


def test_helloyishi_sponsored_and_non_article():
    assert helloyishi.parse(hy_page(is_sponsored=True), "u") == {"skip": "sponsored"}
    assert helloyishi.parse("<html>no data</html>", "u") is None
    assert helloyishi.parse(hy_page(post_content=""), "u") is None


def test_helloyishi_subcategory_filter_and_discovery():
    assert helloyishi.subcategory_of("https://helloyishi.com.tw/healthy-eating/obesity/a/") == "obesity"
    assert helloyishi.subcategory_of("https://helloyishi.com.tw/healthy-eating/nutrition/") is None
    assert helloyishi.subcategory_of("https://helloyishi.com.tw/drugs-supplement/fish-oil/") is None
    index = ("<sitemapindex><sitemap><loc>https://helloyishi.com.tw/sitemap/sitemap-pt-post-2026-01.xml</loc></sitemap>"
             "<sitemap><loc>https://helloyishi.com.tw/sitemap/sitemap-pt-healthtool-2026-01.xml</loc></sitemap></sitemapindex>")
    month = ("<urlset><url><loc>https://helloyishi.com.tw/healthy-eating/nutrition/a/</loc></url>"
             "<url><loc>https://helloyishi.com.tw/diabetes/b/</loc></url>"
             "<url><loc>https://helloyishi.com.tw/healthy-eating/special-diets/c/</loc></url></urlset>")
    fetcher = FakeFetcher({helloyishi.SITEMAP_INDEX: index,
                           "https://helloyishi.com.tw/sitemap/sitemap-pt-post-2026-01.xml": month})
    found = helloyishi.discover(fetcher, log=lambda *_: None)
    assert [(c["url"].rsplit("/", 2)[-2], c["subcategory"]) for c in found] == [("a", "營養知識"), ("c", "特殊飲食")]
    assert not any("healthtool" in u for u in fetcher.calls)


def test_healthnews_discovery_reads_all_pages():
    page = lambda ids, last: ("".join(f'<a href="/article/{i}">標題{i}</a>' for i in ids)
                              + f'<ul class="pagination"><li><a data-ci-pagination-page="{last}">{last}</a></li></ul>')
    pages = {healthnews.channel_url(healthnews.NUTRITION_CHANNEL, 1): page([1, 2], 2),
             healthnews.channel_url(healthnews.NUTRITION_CHANNEL, 2): page([2, 3], 2)}
    found = healthnews.discover(FakeFetcher(pages), log=lambda *_: None)
    assert [c["article_id"] for c in found] == ["1", "2", "3"]
    assert all(c["category"] == "營養飲食" for c in found)


def test_crawl_writes_jsonl_skips_and_resumes(tmp_path):
    cands = [{"url": f"https://www.healthnews.com.tw/article/{i}", "article_id": str(i), "category": "營養飲食"}
             for i in (123, 404, 500)]
    folder = crawl.source_dir(tmp_path, "healthnews")
    (folder / "urls.json").write_text(json.dumps({"candidates": cands}), encoding="utf-8")
    pages = {cands[0]["url"]: HN_ARTICLE, cands[1]["url"]: None, cands[2]["url"]: FetchError("boom")}
    fetcher = FakeFetcher(pages)
    counts = crawl.crawl("healthnews", fetcher, tmp_path, log=lambda *_: None)
    assert counts == {"saved": 1, "not_found": 1, "fetch_failed": 1}
    saved = crawl.read_jsonl(folder / "articles.jsonl")
    assert saved[0]["discovered_url"] == cands[0]["url"] and saved[0]["source_name"] == "健康醫療網"

    # 接續：已存與 not_found 不再抓；fetch_failed 重試
    fetcher.pages[cands[2]["url"]] = HN_ARTICLE.replace("/article/123", "/article/500")
    fetcher.calls.clear()
    counts = crawl.crawl("healthnews", fetcher, tmp_path, log=lambda *_: None)
    assert fetcher.calls == [cands[2]["url"]] and counts == {"saved": 1}
    summary = crawl.stats(tmp_path)["healthnews"]
    assert summary["articles"] == 2 and summary["date_range"] == ["2026-10-01", "2026-10-01"]


def test_crawl_tolerates_truncated_last_line(tmp_path):
    folder = crawl.source_dir(tmp_path, "helloyishi")
    (folder / "articles.jsonl").write_text('{"url": "a", "discovered_url": "a"}\n{"url": "b", "disc', encoding="utf-8")
    assert [r["url"] for r in crawl.read_jsonl(folder / "articles.jsonl")] == ["a"]


def test_data_dir_must_be_outside_repository(tmp_path, monkeypatch):
    monkeypatch.delenv("ASSISTANT_DATA_DIR", raising=False)
    monkeypatch.delenv("PROJECT_DATA_ROOT", raising=False)
    with pytest.raises(RuntimeError):
        config.data_dir()
    with pytest.raises(RuntimeError):
        config.data_dir(config.PROJECT_ROOT / "data")
    monkeypatch.setenv("PROJECT_DATA_ROOT", str(tmp_path))
    assert config.data_dir() == (tmp_path / "assistant_rag").resolve()


def test_block_parser_root_scope_and_nesting():
    html = '<p>外面</p><div id="r"><ul><li>一<p>二</p></li></ul><script>x</script><p>三</p></div><p>外</p>'
    assert blocks(html, root_id="r") == [("li", "一二"), ("p", "三")]
    assert to_sections([("p", "a"), ("h3", "孤立小標"), ("p", "b")])[1]["heading_path"] == ["孤立小標"]


def test_supplement_urls_are_merged_tagged_and_host_checked(tmp_path):
    folder = crawl.source_dir(tmp_path, "healthnews")
    listed = {"url": "https://www.healthnews.com.tw/article/1", "article_id": "1", "category": "營養飲食"}
    (folder / "urls.json").write_text(json.dumps({"candidates": [listed]}), encoding="utf-8")
    extra = "https://www.healthnews.com.tw/article/123"
    assert crawl.add_supplement("healthnews", tmp_path, [extra, listed["url"], extra], "T11 來源") == 2
    assert crawl.add_supplement("healthnews", tmp_path, [extra], "T11 來源") == 0
    with pytest.raises(ValueError):
        crawl.add_supplement("healthnews", tmp_path, ["https://evil.example/article/9"], "x")
    fetcher = FakeFetcher({listed["url"]: None, extra: HN_ARTICLE})
    crawl.crawl("healthnews", fetcher, tmp_path, log=lambda *_: None)
    assert fetcher.calls == [listed["url"], extra]  # 列表內的網址不因補抓重複
    saved = crawl.read_jsonl(folder / "articles.jsonl")
    assert saved[0]["category"] == "指定補抓" and saved[0]["supplement_reason"] == "T11 來源"


OLD_HN = """<html><head><meta property="og:title" content="吃甘藍菜好處 - 健康醫療網"/>
<meta property="og:url" content="https://www.healthnews.com.tw/article/13547"/></head><body>
<div id="article-content" class="article-content-20"><div><span>甘藍菜富含維生素K。（圖片來源／取自網路）</span></div>
<div>&nbsp;</div><div><span>營養師建議選葉脈細的甘藍菜。</span></div></div><div>頁尾</div></body></html>"""


def test_healthnews_old_layout_without_p_uses_divs():
    r = healthnews.parse(OLD_HN, "https://www.healthnews.com.tw/article/13547")
    assert r["title"] == "吃甘藍菜好處"
    assert r["sections"][0]["paragraphs"] == ["甘藍菜富含維生素K。（圖片來源／取自網路）", "營養師建議選葉脈細的甘藍菜。"]
    # 有 <p> 的新版不受影響：外層 div 的字不會混進來
    new = HN_ARTICLE
    assert healthnews.parse(new, "https://www.healthnews.com.tw/article/123")["sections"] == \
        to_sections([b for b in blocks(new, root_id="article-content") if b[0] != "p" or "內容授權提供" not in b[1]],
                    keep=lambda t: not healthnews.NOT_CONTENT.match(t))


def test_crawl_retry_reason(tmp_path):
    folder = crawl.source_dir(tmp_path, "healthnews")
    url = "https://www.healthnews.com.tw/article/13547"
    (folder / "urls.json").write_text(json.dumps({"candidates": [{"url": url, "article_id": "13547"}]}), encoding="utf-8")
    (folder / "skipped.jsonl").write_text(json.dumps({"url": url, "reason": "parse_failed"}) + "\n", encoding="utf-8")
    fetcher = FakeFetcher({url: OLD_HN})
    assert crawl.crawl("healthnews", fetcher, tmp_path, log=lambda *_: None) == {}
    assert crawl.crawl("healthnews", fetcher, tmp_path, retry=frozenset({"parse_failed"}), log=lambda *_: None) == {"saved": 1}
    assert crawl.crawl("healthnews", fetcher, tmp_path, retry=frozenset({"parse_failed"}), log=lambda *_: None) == {}


def test_healthnews_oldest_layout_br_paragraphs():
    html = OLD_HN.replace('<div><span>甘藍菜富含維生素K。（圖片來源／取自網路）</span></div>\n<div>&nbsp;</div><div><span>營養師建議選葉脈細的甘藍菜。</span></div>',
                          '<span>甘藍菜富含維生素K。<br />\n<br />\n營養師建議<br/>選葉脈細的甘藍菜。</span>')
    r = healthnews.parse(html, "https://www.healthnews.com.tw/article/13547")
    assert r["sections"][0]["paragraphs"] == ["甘藍菜富含維生素K。", "營養師建議 選葉脈細的甘藍菜。"]
