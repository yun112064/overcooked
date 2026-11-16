"""文章 → 檢索片段（步驟 2）。

- 逐段落累積到約 400 字為一片；超過的長段落依句號切成 400 字視窗、重疊約 80 字。
- 相鄰片段重疊：下一片開頭帶入上一片最後一兩句（合計不超過 80 字）。
- 換到新的 h2 小節時另起一片（目前片段不足 150 字時則併入，避免過碎）；h3 小標以「▸小標」寫進片段文字。
- 每片的 embed_text 前綴「標題｜小標」，讓向量與 BM25 都看得到主題。
- 與文中小標完全相同的段落視為文章目錄，不收。
- 內容雜湊相同的文章只收第一篇（重複轉載）。
"""
import re
from dataclasses import dataclass, field

CHUNKER_VERSION = "assistant-chunk-v1"
TARGET = 400
OVERLAP = 80
MIN_SECTION_CHUNK = 150
SENTENCE = re.compile(r"[^。！？!?；;\n]*[。！？!?；;]+|[^。！？!?；;\n]+")


@dataclass
class Chunk:
    chunk_id: str
    article_key: str
    position: int
    heading_path: list[str]
    text: str
    embed_text: str = ""
    tokens: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


def article_key(record: dict) -> str:
    return f"{record['source']}:{record['article_id']}"


def dedupe(records: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """依 content_hash 去重；回傳（保留的文章, {重複文章 key: 保留文章 key}）。"""
    kept, seen, duplicates = [], {}, {}
    for r in records:
        key = article_key(r)
        if r["content_hash"] in seen:
            duplicates[key] = seen[r["content_hash"]]
            continue
        seen[r["content_hash"]] = key
        kept.append(r)
    return kept, duplicates


def sentences(text: str) -> list[str]:
    return [s for s in (m.group(0).strip() for m in SENTENCE.finditer(text)) if s]


def _tail(parts: list[str]) -> list[str]:
    """取最後幾句（合計 ≤OVERLAP 字）當下一片的開頭。"""
    tail: list[str] = []
    for s in reversed(sentences("\n".join(parts))):
        if len(s) + sum(map(len, tail)) > OVERLAP:
            break
        tail.insert(0, s)
    return tail


def _windows(text: str) -> list[str]:
    """長段落 → 依句子組成 ≤TARGET 字的視窗，視窗間重疊約 OVERLAP 字；單句過長時硬切。"""
    pieces: list[str] = []
    for s in sentences(text) or [text]:
        while len(s) > TARGET:
            pieces.append(s[:TARGET])
            s = s[TARGET - OVERLAP:]
        pieces.append(s)
    out: list[str] = []
    current: list[str] = []
    for s in pieces:
        if current and len("".join(current)) + len(s) > TARGET:
            out.append("".join(current))
            current = _tail(current)
        current.append(s)
    if current:
        out.append("".join(current))
    return out


def chunk_article(record: dict) -> list[Chunk]:
    key = article_key(record)
    chunks: list[Chunk] = []
    bodies: list[list[str]] = []  # 每片去掉開頭重疊句後的內容，用於併回過短的最後一片
    state = {"current": [], "overlap": 0, "heading": []}

    def size() -> int:
        return len("\n".join(state["current"]))

    def emit():
        text = "\n".join(state["current"]).strip()
        if text:
            chunks.append(Chunk(f"{key}:{len(chunks)}", key, len(chunks), list(state["heading"]), text))
            bodies.append(state["current"][state["overlap"]:])
        state["current"], state["overlap"] = [], 0

    # 文章目錄：與文中小標完全相同的段落（Hello醫師開頭的目錄清單）不收
    toc = {h for section in record["sections"] for h in section.get("heading_path") or []}
    for section in record["sections"]:
        path = list(section.get("heading_path") or ([section["heading"]] if section.get("heading") else []))
        if path[:1] != state["heading"][:1] and state["current"] and size() >= MIN_SECTION_CHUNK:
            emit()
        if not state["current"]:
            state["heading"] = path
        lines = ([f"▸{path[-1]}"] if len(path) > 1 else []) + [p for p in section["paragraphs"] if p not in toc]
        for line in lines:
            for piece in (_windows(line) if len(line) > TARGET else [line]):
                if state["current"] and size() + len(piece) + 1 > TARGET:
                    previous = state["current"]
                    emit()
                    state["heading"] = path
                    tail = _tail(previous)
                    if len("\n".join(tail)) + len(piece) + 1 <= TARGET:  # 放得下才帶重疊句（長段落視窗本身已有重疊）
                        state["current"], state["overlap"] = tail, len(tail)
                state["current"].append(piece)
    emit()
    # 最後一片太短（只剩重疊句或一兩句）時併回前一片（合併後不超過 TARGET+OVERLAP）
    extra = "\n".join(bodies[-1]).strip() if bodies else ""
    if (len(chunks) > 1 and chunks[-1].char_count < OVERLAP + 40
            and chunks[-2].char_count + len(extra) + 1 <= TARGET + OVERLAP):
        chunks.pop()
        if extra:
            chunks[-1].text += "\n" + extra
    for c in chunks:
        prefix = record["title"] + ("｜" + " > ".join(c.heading_path) if c.heading_path else "")
        c.embed_text = f"{prefix}\n{c.text}"
    return chunks
