"""BM25 Okapi（k1=1.5、b=0.75）。片段斷詞存在資料庫 chunks.tokens，載入時在記憶體建索引。"""
import math
from collections import Counter

K1 = 1.5
B = 0.75


class BM25:
    def __init__(self, ids: list[str], docs: list[list[str]], *, k1: float = K1, b: float = B):
        if len(ids) != len(docs):
            raise ValueError("ids 與 docs 數量不同")
        self.ids, self.k1, self.b = list(ids), k1, b
        self.lengths = [len(d) for d in docs]
        self.avg = (sum(self.lengths) / len(docs)) if docs else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for i, doc in enumerate(docs):
            for term, tf in Counter(doc).items():
                self.postings.setdefault(term, []).append((i, tf))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def search(self, query_tokens: list[str], k: int = 50) -> list[tuple[str, float]]:
        scores: dict[int, float] = {}
        for term in set(query_tokens):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in self.postings[term]:
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / norm
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self.ids[i], s) for i, s in ranked]
