"""文章 HTML → 依小標分段的純文字（兩個來源共用）。

只取標題（h2–h4）與內文區塊（p、li、blockquote、td）；圖片、腳本、按鈕等忽略。
舊版文章沒有 <p>、內文直接放在 <div> 裡，可用 block_tags 把 div 也當內文區塊（最內層有字的 div 為一段）。
小標決定段落歸屬：h2 開新段，h3／h4 接在目前 h2 之下（heading_path＝[h2, h3]）。
"""
import re
from html.parser import HTMLParser

HEADINGS = {"h2": 1, "h3": 2, "h4": 3}
BLOCKS = {"p", "li", "blockquote", "td"}
SKIP = {"script", "style", "noscript", "figcaption", "button", "svg", "iframe"}


# 兩站共用的非內文：「（延伸閱讀：…）」站內推薦、WordPress 短代碼（例如 [embed-health-tool-bmi]）
BOILERPLATE = re.compile(r"^([（(【]\s*延伸閱讀|延伸閱讀[:：]|\[[a-z0-9_-]+\]$)")


def is_boilerplate(text: str) -> bool:
    return bool(BOILERPLATE.match(text))


def clean(text: str) -> str:
    """壓縮空白；保留原文字元（全形標點不改）。"""
    return re.sub(r"\s+", " ", text.replace("　", " ").replace("\xa0", " ")).strip()


class _BlockParser(HTMLParser):
    def __init__(self, root_id: str | None, block_tags=BLOCKS):
        super().__init__(convert_charrefs=True)
        self.root_id = root_id
        self.block_tags = block_tags
        self.root_depth = 0 if root_id else 1  # >0 代表在擷取範圍內（以 div 深度追蹤）
        self.skip = 0
        self.blocks: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._nest = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.root_id:
            if tag == "div":
                if self.root_depth:
                    self.root_depth += 1
                elif dict(attrs).get("id") == self.root_id:
                    self.root_depth = 1
                    return
        if not self.root_depth:
            return
        if tag in SKIP:
            self.skip += 1
        elif tag == "br" and self._tag:
            self._buf.append(" ")
        elif tag in HEADINGS or tag in self.block_tags:
            # 小標一律另起一段：WordPress 常把 <h2> 包在 <p> 裡；空的外層區塊也讓給內層
            if self._tag is not None and (tag in HEADINGS or not clean("".join(self._buf))):
                self._flush()
            if self._tag is None:
                self._tag, self._nest, self._buf = tag, 1, []
            elif tag == self._tag:
                self._nest += 1

    def handle_endtag(self, tag):
        if self.root_id and tag == "div" and self.root_depth:
            self.root_depth -= 1
            if not self.root_depth:
                self._flush()
                return
            # 仍在擷取範圍內：div 也可能是內文區塊，交給下面關閉
        if not self.root_depth:
            return
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag == self._tag:
            self._nest -= 1
            if self._nest <= 0:
                self._flush()

    def handle_data(self, data):
        if self.root_depth and self._tag and not self.skip:
            self._buf.append(data)

    def _flush(self):
        if self._tag:
            text = clean("".join(self._buf))
            if text:
                self.blocks.append((self._tag, text))
        self._tag, self._nest, self._buf = None, 0, []

    def close(self):
        super().close()
        self._flush()


def blocks(html: str, root_id: str | None = None, block_tags=BLOCKS) -> list[tuple[str, str]]:
    parser = _BlockParser(root_id, frozenset(block_tags))
    parser.feed(html)
    parser.close()
    return parser.blocks


def to_sections(items: list[tuple[str, str]], keep=lambda text: True) -> list[dict]:
    """[(tag, text)] → [{heading, heading_path, paragraphs}]；第一段（前言）heading 為 None。"""
    sections: list[dict] = []
    path: list[str] = []
    current = {"heading": None, "heading_path": [], "paragraphs": []}
    for tag, text in items:
        if tag in HEADINGS:
            if current["paragraphs"]:
                sections.append(current)
            level = HEADINGS[tag]
            path = path[:level - 1] + [text] if level > 1 and path else [text]
            current = {"heading": text, "heading_path": list(path), "paragraphs": []}
        elif keep(text):
            current["paragraphs"].append(text)
    if current["paragraphs"]:
        sections.append(current)
    return sections
