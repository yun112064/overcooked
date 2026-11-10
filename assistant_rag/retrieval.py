"""步驟 3：六種檢索方法（簡報第 6 頁＋使用者選定的組合）。

| 方法 | 做法 |
|---|---|
| bm25 | jieba 斷詞 BM25，取前 50 |
| vector | 向量精確 cosine，取前 50 |
| hybrid | BM25 前 50＋向量前 50，RRF：Σ 1/(60+名次) |
| hybrid_rerank | hybrid 前 30 段（每篇最多 3 段）交給 bge-reranker-v2-m3 重排 |
| entity_boost | hybrid_rerank 後，問題中辨識出的食材出現在文章標題 +0.15、只出現在片段內文 +0.05（「地瓜葉」不算提到「地瓜」） |
| multi_query | qwen3.5 把問題改寫成 3 句；原句＋改寫各跑 hybrid，再以 RRF 合併，前 30 用原句重排 |

最後每篇文章只留分數最高的一段，回傳前 5 篇。vector 以後的方法都要指定嵌入模型（bge-m3／embeddinggemma），
加上 bm25 共 11 組比較。分數意義：bm25 為 BM25 分、vector 為 cosine、hybrid／multi_query 的 RRF 分、
rerank 系列為重排機率（0–1；entity_boost 可略高於 1）。
"""
import json
import re
import time
from dataclasses import asdict, dataclass, field

from assistant_rag import store
from assistant_rag.bm25 import BM25
from assistant_rag.providers import EMBEDDERS, ProviderUnavailable, chat_model, embedder, reranker
from assistant_rag.segment import default_tokenizer, food_terms

METHODS = ("bm25", "vector", "hybrid", "hybrid_rerank", "entity_boost", "multi_query")
NEEDS_EMBEDDER = set(METHODS) - {"bm25"}
CANDIDATES = 50
RRF_K = 60
RERANK_TOP = 30
PER_ARTICLE = 3  # 送重排的 30 段中，每篇最多 3 段，避免長文章佔滿候選、最後湊不滿 5 篇
TOP_ARTICLES = 5
TITLE_BOOST = 0.15
TEXT_BOOST = 0.05
REWRITES = 3
REWRITE_PROMPT = (
    "你是飲食知識庫的搜尋查詢改寫器。把使用者的問題改寫成 3 個說法不同的繁體中文搜尋查詢，"
    "可加入同義詞、相關食物或營養素，但不可改變原意，也不可加入原問題沒有的疾病或族群。"
    '只輸出 JSON：{"queries": ["…", "…", "…"]}'
)


def combos() -> list[tuple[str, str | None]]:
    """11 組比較：bm25 ＋ 其餘 5 種方法 × 2 個嵌入模型。"""
    return [("bm25", None)] + [(m, e) for m in METHODS if m in NEEDS_EMBEDDER for e in EMBEDDERS]


def label(method: str, emb: str | None) -> str:
    return method if emb is None else f"{method}@{emb}"


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class Hit:
    rank: int
    chunk_id: str
    article_key: str
    score: float
    title: str
    url: str
    source_name: str
    heading_path: list[str]
    text: str
    published_at: str | None


@dataclass
class Result:
    query: str
    method: str
    embedder: str | None
    hits: list[Hit]
    timings: dict = field(default_factory=dict)  # 秒
    extras: dict = field(default_factory=dict)   # 改寫句、辨識食材、候選數等

    @property
    def label(self) -> str:
        return label(self.method, self.embedder)

    def to_dict(self) -> dict:
        return {"query": self.query, "method": self.label, "timings": self.timings, "extras": self.extras,
                "hits": [asdict(h) for h in self.hits]}


class Retriever:
    """載入 active 索引（片段與 BM25 在記憶體），提供六種方法。模型只在用到時才建立。"""

    def __init__(self, conn, version: str | None = None, *, tokenizer=None, embedders=None,
                 reranker_model=None, chat=None, foods: set[str] | None = None, rows: list | None = None):
        self.conn = conn
        self.version = version or store.active_version(conn)
        if not self.version:
            raise RuntimeError("沒有 active 索引，請先執行 python -m assistant_rag.index build")
        self.tokenizer = tokenizer or default_tokenizer()
        self._embedders = dict(embedders or {})
        self._reranker = reranker_model
        self._chat = chat
        self.foods = foods if foods is not None else set(food_terms())
        self._rewrites: dict[str, list[str]] = {}
        self._longer: dict[str, list[str]] = {}
        rows = rows if rows is not None else conn.execute(
            "SELECT c.chunk_id, c.article_key, c.heading_path, c.text, c.embed_text, c.tokens, a.title, a.url,"
            " a.source_name, a.published_at FROM chunks c JOIN articles a USING (version, article_key)"
            " WHERE c.version = %s ORDER BY c.chunk_id", (self.version,)).fetchall()
        self.chunks = {r[0]: {"article_key": r[1], "heading_path": list(r[2]), "text": r[3], "embed_text": r[4],
                              "title": r[6], "url": r[7], "source_name": r[8],
                              "published_at": r[9].isoformat() if r[9] else None} for r in rows}
        self.bm25_index = BM25([r[0] for r in rows], [list(r[5]) for r in rows])

    # ---------- 模型 ----------
    def embedder(self, name: str):
        if name not in self._embedders:
            self._embedders[name] = embedder(name)
        return self._embedders[name]

    def reranker(self):
        if self._reranker is None:
            self._reranker = reranker()
        return self._reranker

    def chat(self):
        if self._chat is None:
            self._chat = chat_model()
        return self._chat

    # ---------- 基本檢索 ----------
    def bm25(self, query: str, k: int = CANDIDATES) -> list[tuple[str, float]]:
        return self.bm25_index.search(self.tokenizer.tokens(query), k)

    def vector(self, query: str, emb: str, k: int = CANDIDATES) -> list[tuple[str, float]]:
        return store.vector_search(self.conn, self.version, emb, self.embedder(emb).query(query), k)

    def hybrid(self, query: str, emb: str, k: int = CANDIDATES) -> list[tuple[str, float]]:
        return rrf([[c for c, _ in self.bm25(query, k)], [c for c, _ in self.vector(query, emb, k)]])

    def pool(self, ranked: list[tuple[str, float]], n: int = RERANK_TOP, per_article: int = PER_ARTICLE) -> list[str]:
        """依名次取 n 段送重排，每篇最多 per_article 段。"""
        out, count = [], {}
        for cid, _ in ranked:
            key = self.chunks[cid]["article_key"]
            if count.get(key, 0) < per_article:
                count[key] = count.get(key, 0) + 1
                out.append(cid)
                if len(out) == n:
                    break
        return out

    def rerank(self, query: str, candidates: list[str]) -> list[tuple[str, float]]:
        scores = self.reranker().rerank(query, [self.chunks[c]["embed_text"] for c in candidates])
        return sorted(zip(candidates, scores), key=lambda kv: (-kv[1], kv[0]))

    def entities(self, query: str) -> list[str]:
        """問題中的食材：斷詞結果在食材詞典內，去掉被更長食材詞包含的（「地瓜葉」優先於「地瓜」）。"""
        found = sorted({t for t in self.tokenizer.tokens(query) if t in self.foods}, key=len, reverse=True)
        return [t for i, t in enumerate(found) if not any(t in longer for longer in found[:i])]

    def rewrite(self, query: str) -> list[str]:
        if query not in self._rewrites:
            raw = self.chat().complete([{"role": "system", "content": REWRITE_PROMPT},
                                        {"role": "user", "content": query}], json_mode=True)
            try:
                items = json.loads(raw).get("queries", [])
            except (ValueError, AttributeError):
                items = []
            seen, out = {query}, []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, str) and (text := re.sub(r"\s+", " ", item).strip()) and text not in seen:
                    seen.add(text)
                    out.append(text)
            self._rewrites[query] = out[:REWRITES]
        return self._rewrites[query]

    # ---------- 六種方法 ----------
    def search(self, query: str, method: str, emb: str | None = None, *, top: int = TOP_ARTICLES) -> Result:
        if method not in METHODS:
            raise ValueError(f"未知的方法：{method}（可用：{', '.join(METHODS)}）")
        if method in NEEDS_EMBEDDER and emb not in EMBEDDERS:
            raise ValueError(f"{method} 需要指定嵌入模型：{', '.join(EMBEDDERS)}")
        if method == "bm25":
            emb = None
        timings: dict[str, float] = {}
        extras: dict = {}
        start = time.perf_counter()

        def mark(name: str, since: float) -> float:
            now = time.perf_counter()
            timings[name] = round(now - since, 3)
            return now

        t = start
        if method == "bm25":
            ranked = self.bm25(query)
            t = mark("bm25", t)
        elif method == "vector":
            ranked = self.vector(query, emb)
            t = mark("vector", t)
        elif method == "multi_query":
            try:
                rewrites = self.rewrite(query)
            except ProviderUnavailable as exc:
                rewrites, extras["rewrite_error"] = [], exc.code
            extras["rewrites"] = rewrites
            t = mark("rewrite", t)
            lists = [[c for c, _ in self.hybrid(q, emb)] for q in [query, *rewrites]]
            fused = rrf(lists)
            extras["candidates"] = len(fused)
            t = mark("retrieve", t)
            ranked = self.rerank(query, self.pool(fused))
            t = mark("rerank", t)
        else:
            ranked = self.hybrid(query, emb)
            extras["candidates"] = len(ranked)
            t = mark("retrieve", t)
            if method in ("hybrid_rerank", "entity_boost"):
                ranked = self.rerank(query, self.pool(ranked))
                t = mark("rerank", t)
            if method == "entity_boost":
                foods = self.entities(query)
                extras["entities"] = foods
                if foods:
                    ranked = sorted(((c, s + self._boost(c, foods)) for c, s in ranked), key=lambda kv: (-kv[1], kv[0]))
        hits = self._top_articles(ranked, top)
        timings["total"] = round(time.perf_counter() - start, 3)
        return Result(query, method, emb, hits, timings, extras)

    def mentions(self, text: str, food: str) -> bool:
        """text 是否提到 food 本身；「地瓜葉」「地瓜粉」這類包含它的更長食材名不算。"""
        if food not in self._longer:
            self._longer[food] = sorted((f for f in self.foods if food in f and f != food), key=len, reverse=True)
        for longer in self._longer[food]:
            text = text.replace(longer, "")
        return food in text

    def _boost(self, cid: str, foods: list[str]) -> float:
        chunk = self.chunks[cid]
        if any(self.mentions(chunk["title"], f) for f in foods):
            return TITLE_BOOST
        if any(self.mentions(chunk["text"], f) for f in foods):
            return TEXT_BOOST
        return 0.0

    def _top_articles(self, ranked: list[tuple[str, float]], top: int) -> list[Hit]:
        hits, seen = [], set()
        for cid, score in ranked:
            chunk = self.chunks[cid]
            if chunk["article_key"] in seen:
                continue
            seen.add(chunk["article_key"])
            hits.append(Hit(len(hits) + 1, cid, chunk["article_key"], round(float(score), 4), chunk["title"],
                            chunk["url"], chunk["source_name"], chunk["heading_path"], chunk["text"],
                            chunk["published_at"]))
            if len(hits) == top:
                break
        return hits
