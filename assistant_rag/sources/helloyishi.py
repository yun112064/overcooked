"""Hello醫師（helloyishi.com.tw）「健康飲食」分類。

- 列表：sitemap 索引 → 每月文章 sitemap（sitemap-pt-post-YYYY-MM.xml）→ 只留健康飲食四個子分類的網址。
- 文章：Next.js 的 __NEXT_DATA__（pageProps.articlesV2[0]）：標題、日期、主分類、撰文／資料查核／醫學審稿、
  post_content（h2／h3 小標＋內文）、sources（參考資料）、is_sponsored。贊助文章不收錄。
"""
import html as html_lib
import json
import re
from urllib.parse import urlsplit

from assistant_rag.htmltext import blocks, clean, is_boilerplate, to_sections
from assistant_rag.sources import finish_record

SOURCE = "helloyishi"
SOURCE_NAME = "Hello醫師"
HOST = "helloyishi.com.tw"
SITE = f"https://{HOST}"
SITEMAP_INDEX = f"{SITE}/sitemap/sitemap.xml"
SUBCATEGORIES = {"nutrition": "營養知識", "special-diets": "特殊飲食",
                 "other-healthy-eating-tips": "健康飲食小撇步", "obesity": "肥胖"}
CATEGORY = "健康飲食"
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
POST_SITEMAP = re.compile(r"/sitemap-pt-post-\d{4}-\d{2}\.xml$")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
DESCRIPTION = re.compile(r'<meta name="description" content="([^"]*)"')
REVIEW_ROLES = {"author": "撰文", "expert": "醫學審稿", "fact_check": "資料查核", "editor": "編輯"}
# 文章目錄、圖片授權等非內文
NOT_CONTENT = re.compile(r"^(文章目錄|圖片授權|圖片來源|延伸閱讀|【延伸閱讀】|Hello醫師提醒您[:：]?$)")


def subcategory_of(url: str) -> str | None:
    """/healthy-eating/<子分類>/<文章>/ → 子分類代稱；其他網址回傳 None。"""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) == 3 and parts[0] == "healthy-eating" and parts[1] in SUBCATEGORIES:
        return parts[1]
    return None


def discover(fetcher, *, max_sitemaps: int | None = None, log=print) -> list[dict]:
    index = fetcher.get(SITEMAP_INDEX) or ""
    sitemaps = sorted({u for u in LOC.findall(index) if POST_SITEMAP.search(u)}, reverse=True)
    if max_sitemaps is not None:
        sitemaps = sitemaps[:max_sitemaps]
    found: dict[str, dict] = {}
    for i, sitemap in enumerate(sitemaps, 1):
        for url in LOC.findall(fetcher.get(sitemap) or ""):
            url = html_lib.unescape(url)
            if (sub := subcategory_of(url)) and url not in found:
                found[url] = {"url": url, "article_id": None, "title_hint": None,
                              "category": CATEGORY, "subcategory": SUBCATEGORIES[sub]}
        if i % 20 == 0:
            log(f"[{SOURCE}] sitemap {i}/{len(sitemaps)}，累計 {len(found)} 篇")
    return list(found.values())


def _local_time(value: str | None) -> str | None:
    """WordPress 本地時間 'YYYY-MM-DD HH:MM:SS'（台灣）→ ISO 8601 +08:00。"""
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        return None
    return value.replace(" ", "T") + "+08:00"


def _name(person) -> str | None:
    name = person.get("display_name") if isinstance(person, dict) else None
    return clean(name) if isinstance(name, str) and name.strip() else None


def _faq_section(faqs) -> dict | None:
    paragraphs = []
    for item in faqs if isinstance(faqs, list) else []:
        if isinstance(item, dict):
            q, a = item.get("question"), item.get("answer")
            if isinstance(q, str) and isinstance(a, str):
                answer = " ".join(t for _, t in blocks(a)) or clean(re.sub(r"<[^>]+>", " ", a))
                if clean(q) and answer:
                    paragraphs.append(f"{clean(q)} {answer}")
    return {"heading": "常見問題", "heading_path": ["常見問題"], "paragraphs": paragraphs} if paragraphs else None


def parse(html: str, url: str, hint: dict | None = None) -> dict | None:
    """文章頁 → 統一紀錄；非文章頁、無內文回傳 None；贊助文章回傳 {"skip": "sponsored"}。"""
    match = NEXT_DATA.search(html)
    if not match:
        return None
    try:
        articles = json.loads(match.group(1))["props"]["pageProps"].get("articlesV2") or []
    except (ValueError, KeyError, TypeError):
        return None
    if not articles or not isinstance(articles[0], dict):
        return None
    a = articles[0]
    if a.get("is_sponsored") or a.get("sponsor"):
        return {"skip": "sponsored"}
    title = clean(a.get("post_title") or "")
    sections = to_sections(blocks(a.get("post_content") or ""),
                           keep=lambda t: not NOT_CONTENT.match(t) and not is_boilerplate(t))
    permalink = a.get("permalink") or a.get("permalink_decode") or ""  # 編碼過的網址，可直接當連結
    if not title or not sections or a.get("ID") is None:  # 沒有正文（只有常見問題）不收
        return None
    if (faq := _faq_section(a.get("faqs"))):
        sections.append(faq)
    reviewed = a.get("reviewed") if isinstance(a.get("reviewed"), dict) else {}
    reviewers = {label: name for key, label in REVIEW_ROLES.items() if (name := _name(reviewed.get(key)))}
    primary = a.get("primary_category") if isinstance(a.get("primary_category"), dict) else {}
    description = DESCRIPTION.search(a.get("meta_tag") or "")
    references = [t for _, t in blocks(a.get("sources") or "")]
    categories = [clean(c["name"]) for c in a.get("categories") or [] if isinstance(c, dict) and c.get("name")]
    return finish_record({
        "source": SOURCE, "source_name": SOURCE_NAME, "article_id": str(a["ID"]),
        "url": SITE + permalink if permalink.startswith("/") else url, "title": title,
        "published_at": _local_time(a.get("post_date")), "modified_at": _local_time(a.get("post_modified")),
        "author": reviewers.get("撰文"), "reviewers": reviewers, "content_provider": None,
        "category": CATEGORY, "subcategory": clean(primary.get("name") or "") or (hint or {}).get("subcategory"),
        "keywords": categories, "description": clean(html_lib.unescape(description.group(1))) if description else None,
        "references": references, "is_sponsored": False, "sections": sections,
    })
