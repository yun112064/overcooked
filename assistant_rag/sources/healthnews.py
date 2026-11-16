"""健康醫療網（www.healthnews.com.tw）「營養飲食」頻道。

- 列表：/channel/<營養飲食頻道>/<頁碼>（沿用 knowledge_rag.healthnews.parse_listing，只讀程式不動其資料）。
- 文章：<div id="article-content"> 內 h2 小標＋p 內文（2017 年前的舊文章沒有 p，內文在 div 裡，改以 div 為段落）；「▲」開頭為圖說、文末「（內容授權提供／X）」為供稿者。
  meta keywords／description、article:published_time、ld+json 作者。
"""
import json
import re

from assistant_rag.htmltext import BLOCKS, HEADINGS, blocks, clean, is_boilerplate, to_sections
from assistant_rag.sources import finish_record
from knowledge_rag.healthnews import NUTRITION_CHANNEL, article_url, channel_url, parse_listing

SOURCE = "healthnews"
SOURCE_NAME = "健康醫療網"
HOST = "www.healthnews.com.tw"
CATEGORY = "營養飲食"
ARTICLE_ID = re.compile(r"/article/(\d+)")
PROVIDER = re.compile(r"^[（(]\s*內容授權提供\s*[／/]\s*(.+?)\s*[）)]$")
TITLE_SUFFIX = re.compile(r"\s*[-－|｜]\s*健康醫療網(?:\s*[-－|｜]\s*健康養生新聞資訊網路媒體)?\s*$")
META = re.compile(r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"', re.I)
LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
# 圖說、宣傳與分享連結等非內文
ROOT_OPEN = re.compile(r'<div id="article-content"[^>]*>')
BR_PARAGRAPH = re.compile(r"(?:<br\b[^>]*>\s*){2,}", re.I)  # br 可能帶 style 屬性
NOT_CONTENT = re.compile(r"^(▲|圖／|圖/|延伸閱讀|加入【健康醫療網】|訂閱【健康愛樂活】)")


def clean_title(title: str) -> str:
    """去掉「 - 健康醫療網 - 健康養生新聞資訊網路媒體」等網站後綴。"""
    return TITLE_SUFFIX.sub("", clean(title or ""))


def discover(fetcher, *, max_pages: int | None = None, log=print) -> list[dict]:
    """逐頁讀營養飲食頻道列表，回傳 [{url, article_id, title_hint, category}]（依頁面順序、去重）。"""
    found: dict[str, dict] = {}
    page, last = 1, 1
    while page <= last and (max_pages is None or page <= max_pages):
        html = fetcher.get(channel_url(NUTRITION_CHANNEL, page))
        if html is None:
            break
        listing = parse_listing(html)
        last = max(last, listing.last_page)
        for article_id, title in listing.articles:
            url = article_url(article_id)
            found.setdefault(url, {"url": url, "article_id": article_id, "title_hint": title, "category": CATEGORY})
        if page % 20 == 0:
            log(f"[{SOURCE}] 列表 {page}/{last} 頁，累計 {len(found)} 篇")
        page += 1
    return list(found.values())


def _ld_author(html: str) -> str | None:
    for blob in LD.findall(html):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") in ("Article", "NewsArticle"):
                author = node.get("author")
                if isinstance(author, list):
                    author = author[0] if author else None
                name = author.get("name") if isinstance(author, dict) else author
                return clean(name) if isinstance(name, str) and name.strip() else None
    return None


def parse(html: str, url: str, hint: dict | None = None) -> dict | None:
    """文章頁 → 統一紀錄；沒有正文或標題回傳 None。"""
    meta: dict[str, str] = {}
    for key, value in META.findall(html):
        meta.setdefault(key.lower(), value)
    match = ARTICLE_ID.search(meta.get("og:url") or url) or ARTICLE_ID.search(url)
    title = clean_title(meta.get("og:title", ""))
    items = blocks(html, root_id="article-content")
    if not any(tag == "p" for tag, _ in items):  # 舊版版面：內文在 div 裡
        items = blocks(html, root_id="article-content", block_tags=BLOCKS | {"div"})
    if all(tag in HEADINGS for tag, _ in items):  # 更舊的版面（可能只有小標）：內文直接放在根節點，以 <br><br> 分段
        items = blocks(BR_PARAGRAPH.sub("</p><p>", ROOT_OPEN.sub(r"\g<0><p>", html, count=1)), root_id="article-content")
    provider = None
    for i in range(len(items) - 1, -1, -1):  # 供稿者在文末，可能後面還有空白段
        if items[i][0] == "p" and (m := PROVIDER.match(items[i][1])):
            provider = m.group(1)
            del items[i]
            break
    sections = to_sections(items, keep=lambda t: not NOT_CONTENT.match(t) and not is_boilerplate(t))
    if not match or not title or not sections:
        return None
    keywords = [clean(k) for k in re.split(r"[,，、]", meta.get("keywords", "")) if clean(k)]
    return finish_record({
        "source": SOURCE, "source_name": SOURCE_NAME, "article_id": match.group(1),
        "url": article_url(match.group(1)), "title": title,
        "published_at": meta.get("article:published_time") or None,
        "modified_at": meta.get("article:modified_time") or None,
        "author": _ld_author(html), "reviewers": {}, "content_provider": provider,
        "category": (hint or {}).get("category", CATEGORY), "subcategory": None,
        "keywords": keywords, "description": clean(meta.get("description", "")) or None,
        "references": [], "is_sponsored": False, "sections": sections,
    })
