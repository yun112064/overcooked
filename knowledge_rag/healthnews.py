"""G07 健康醫療網（https://www.healthnews.com.tw/）來源轉接器：純解析，不做網路存取。

- 分類頁（伺服器端 HTML）：/channel/<頻道 ID>/[頁碼] 列出 /article/<ID>；分頁在 <ul class="pagination">，
  每個頁碼帶 data-ci-pagination-page。熱門關鍵字等導覽連結是絕對網址，不視為列表項目。
- 文章頁：og:title、og:url、article:published_time／modified_time、ld+json（Article）、
  <div id="article-content"> 內的 <p> 正文；文末「（內容授權提供／X）」記為原始供稿者。
- 站內搜尋是 Google CSE、sitemap 停在 2015，皆不使用（decisions #14）。

食材比對只用標準名稱與無歧義別名（與 T14 FoodDictionary 同規則），單字別名不用、最長名稱優先，
已確認的同義組（config/ingredient_synonyms.json status=confirmed）共享結果。
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

SITE = "https://www.healthnews.com.tw"
SOURCE_NAME = "健康醫療網"
NUTRITION_CHANNEL = "f72c8a39-1105-2663-255a-b925ad108bac"  # 營養飲食
ARTICLE_PATH = re.compile(r"^/article/(\d+)/?$")
ARTICLE_URL = re.compile(r"/article/(\d+)")
PROVIDER = re.compile(r"^[（(]\s*內容授權提供\s*[／/]\s*(.+?)\s*[）)]$")
TITLE_SUFFIX = re.compile(r"\s*[-－|｜]\s*健康醫療網\s*$")
# 以食材名稱開頭但不是食材的營養詞（比對時整詞吃掉，不對應任何 FOOD）
NON_FOOD_TERMS = ("蛋白質", "蛋白酶", "乳酸菌", "乳糖不耐", "油脂", "糖尿病", "鹽分", "糖分")


def channel_url(channel_id: str, page: int = 1) -> str:
    return f"{SITE}/channel/{channel_id}/" + (str(page) if page > 1 else "")


def article_url(article_id: str | int) -> str:
    return f"{SITE}/article/{article_id}"


def _clean(text: str) -> str:
    """只壓縮空白；保留原文字元（全形標點不改），比對時才正規化。"""
    return re.sub(r"\s+", " ", text.replace("　", " ")).strip()


# --- 分類頁 ---

@dataclass
class Listing:
    articles: list[tuple[str, str]]  # (文章 ID, 標題)，依頁面順序、去重
    last_page: int


class _ListingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, list[str]]] = []
        self.pages: list[int] = []
        self._current: list[str] | None = None
        self._active: list[str] | None = None  # 目前頁碼（active 項目沒有 data 屬性）

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and "active" in (a.get("class") or "").split() and "page-item" in (a.get("class") or ""):
            self._active = []
        if tag != "a":
            return
        page = a.get("data-ci-pagination-page")
        if page and page.isdigit():
            self.pages.append(int(page))
        m = ARTICLE_PATH.match(a.get("href") or "")
        if m:
            self._current = []
            self.links.append((m.group(1), self._current))

    def handle_endtag(self, tag):
        if tag == "a":
            self._current = None
        elif tag == "li" and self._active is not None:
            text = "".join(self._active).strip()
            if text.isdigit():
                self.pages.append(int(text))
            self._active = None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)
        if self._active is not None:
            self._active.append(data)


def parse_listing(html: str) -> Listing:
    p = _ListingParser()
    p.feed(html)
    titles: dict[str, str] = {}
    for article_id, parts in p.links:
        title = _clean("".join(parts))
        if title and not titles.get(article_id):
            titles[article_id] = title
        titles.setdefault(article_id, "")
    return Listing(articles=[(i, t) for i, t in titles.items() if t], last_page=max(p.pages, default=1))


# --- 文章頁 ---

@dataclass
class Article:
    article_id: str
    url: str
    title: str
    published_at: str | None
    modified_at: str | None
    paragraphs: list[str]
    content_provider: str | None = None
    author: str | None = None
    section: str | None = None


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.ld: list[str] = []
        self.paragraphs: list[str] = []
        self._content_depth = 0  # 在 #article-content 內的 div 深度
        self._para: list[str] | None = None
        self._ld: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") and a.get("content") is not None:
            self.meta.setdefault(a["property"], a["content"])
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._ld = []
        elif tag == "div":
            if self._content_depth:
                self._content_depth += 1
            elif a.get("id") == "article-content":
                self._content_depth = 1
        elif tag == "p" and self._content_depth:
            self._para = []

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.ld.append("".join(self._ld))
            self._ld = None
        elif tag == "div" and self._content_depth:
            self._content_depth -= 1
        elif tag == "p" and self._para is not None:
            text = _clean("".join(self._para))
            if text:
                self.paragraphs.append(text)
            self._para = None

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif self._para is not None:
            self._para.append(data)


def _ld_article(blobs: list[str]) -> dict:
    for blob in blobs:
        try:
            data = json.JSONDecoder().raw_decode(blob.strip())[0]
        except ValueError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") in ("Article", "NewsArticle"):
                return node
    return {}


def parse_article(html: str, url: str) -> Article | None:
    """解析文章頁；沒有正文（不存在的 ID、首頁導回）回傳 None。"""
    p = _ArticleParser()
    p.feed(html)
    ld = _ld_article(p.ld)
    paragraphs = list(p.paragraphs)
    provider = None
    if paragraphs and (m := PROVIDER.match(paragraphs[-1])):
        provider = m.group(1)
        paragraphs.pop()
    canonical = p.meta.get("og:url") or url
    m = ARTICLE_URL.search(canonical) or ARTICLE_URL.search(url)
    title = TITLE_SUFFIX.sub("", _clean(p.meta.get("og:title") or ld.get("headline") or ""))
    if not paragraphs or not m or not title:
        return None
    author = ld.get("author")
    return Article(
        article_id=m.group(1), url=article_url(m.group(1)), title=title,
        published_at=p.meta.get("article:published_time") or ld.get("datePublished"),
        modified_at=p.meta.get("article:modified_time") or ld.get("dateModified"),
        paragraphs=paragraphs, content_provider=provider,
        author=author.get("name") if isinstance(author, dict) else author if isinstance(author, str) else None,
        section=ld.get("articleSection") if isinstance(ld.get("articleSection"), str) else None)


# --- 段落切分 ---

def split_passages(paragraphs: list[str], max_chars: int = 400) -> list[str]:
    """相鄰短段落合併到 max_chars 內；超長段落依上限切開。小標題跟著下一段。"""
    passages: list[str] = []
    current = ""
    for para in paragraphs:
        pieces = [para[i:i + max_chars] for i in range(0, len(para), max_chars)] or [para]
        for piece in pieces:
            if current and len(current) + 1 + len(piece) > max_chars:
                passages.append(current)
                current = ""
            current = f"{current}\n{piece}" if current else piece
    if current:
        passages.append(current)
    return passages


# --- 食材比對 ---

def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


@dataclass
class FoodMatcher:
    """rows：(food_id, 標準名稱, 別名, 有歧義的別名)，與 ingredient_aliases／T14 FoodDictionary 相同形狀。"""
    rows: list
    synonym_groups: list = field(default_factory=list)
    min_alias_chars: int = 2

    def __post_init__(self):
        self.index: dict[str, set[str]] = {}
        self.names: dict[str, list[str]] = {}
        for food_id, canonical, aliases, ambiguous in self.rows:
            blocked = {_norm(a) for a in ambiguous or [] if isinstance(a, str)}
            names = []
            for name in (canonical, *(aliases or [])):
                if not isinstance(name, str) or not name.strip():
                    continue
                key = _norm(name.strip())
                if len(key) < self.min_alias_chars or (name != canonical and key in blocked):
                    continue
                self.index.setdefault(key, set()).add(food_id)
                names.append(name.strip())
            self.names[food_id] = names
        for term in NON_FOOD_TERMS:
            self.index.setdefault(_norm(term), set())
        self.max_len = max((len(k) for k in self.index), default=0)
        self.groups = [set(g) for g in self.synonym_groups]

    def match(self, text: str) -> set[str]:
        text = _norm(text)
        found: set[str] = set()
        i = 0
        while i < len(text):
            for size in range(min(self.max_len, len(text) - i), 0, -1):
                ids = self.index.get(text[i:i + size])
                if ids is not None:
                    found |= ids
                    i += size
                    break
            else:
                i += 1
        for group in self.groups:
            if found & group:
                found |= group
        return found

    def names_for(self, food_ids) -> list[str]:
        out: list[str] = []
        for food_id in food_ids:
            out += [n for n in self.names.get(food_id, []) if n not in out]
        return out
