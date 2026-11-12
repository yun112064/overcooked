"""R14 步驟 3：六種檢索方法（11 組）的組合邏輯。合成片段、假向量／重排／LLM，不連網、不連資料庫。"""
import json

import pytest

from assistant_rag.providers import ProviderUnavailable
from assistant_rag.retrieval import METHODS, PER_ARTICLE, Retriever, combos, rrf
from assistant_rag.segment import Tokenizer

TOK = Tokenizer(["地瓜", "地瓜葉", "豆腐", "痛風", "蛋白質", "雞蛋"])
DOCS = {  # chunk_id: (article_key, title, text)
    "a:0": ("hy:1", "地瓜好處多", "地瓜富含膳食纖維，有助腸道健康。"),
    "a:1": ("hy:1", "地瓜好處多", "地瓜含有維生素A與鉀。"),
    "a:2": ("hy:1", "地瓜好處多", "地瓜可以蒸或烤。"),
    "a:3": ("hy:1", "地瓜好處多", "地瓜皮也可以吃。"),
    "b:0": ("hy:2", "地瓜葉功效", "地瓜葉含鈣與維生素K。"),
    "c:0": ("hn:3", "痛風飲食", "痛風患者可適量吃豆腐。"),
    "d:0": ("hn:4", "雞蛋蛋白質", "雞蛋是優質蛋白質來源。"),
}


def rows():
    return [(cid, key, [], text, f"{title}\n{text}", TOK.tokens(f"{title}\n{text}"), title, f"https://x/{key}",
             "Hello醫師", None) for cid, (key, title, text) in DOCS.items()]


class FakeReranker:
    def __init__(self, scores):
        self.scores, self.calls = scores, []

    def rerank(self, query, texts):
        self.calls.append(texts)
        return [self.scores.get(t.split("\n", 1)[1], 0.01) for t in texts]


class FakeChat:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete(self, messages, *, json_mode=False, temperature=0.0):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeRetriever(Retriever):
    """向量檢索改為固定排名：vector_rank[query] 或預設依 chunk_id。"""

    vector_rank: dict = {}

    def vector(self, query, emb, k=50):
        order = self.vector_rank.get(query, sorted(self.chunks))
        return [(cid, 1.0 - i / 100) for i, cid in enumerate(order[:k])]


def make(rerank=None, chat=None, vector_rank=None):
    r = FakeRetriever(None, "v", tokenizer=TOK, reranker_model=FakeReranker(rerank or {}),
                      chat=chat or FakeChat('{"queries": []}'), foods={"地瓜", "地瓜葉", "豆腐", "雞蛋"}, rows=rows())
    r.vector_rank = vector_rank or {}
    return r


def test_eleven_combinations():
    pairs = combos()
    assert len(pairs) == 6 and pairs[0] == ("bm25", None)  # bm25＋5 種方法×embeddinggemma
    assert {m for m, _ in pairs} == set(METHODS)


def test_rrf_sums_reciprocal_ranks():
    fused = dict(rrf([["x", "y"], ["y", "z"]]))
    assert fused["y"] == pytest.approx(1 / 62 + 1 / 61)
    assert next(iter(fused)) in {"x", "y"} and max(fused, key=fused.get) == "y"


def test_bm25_and_one_chunk_per_article():
    result = make().search("吃地瓜有什麼好處", "bm25")
    keys = [h.article_key for h in result.hits]
    assert keys[0] == "hy:1" and len(keys) == len(set(keys))
    assert result.embedder is None and result.label == "bm25"


def test_method_requires_embedder():
    with pytest.raises(ValueError):
        make().search("地瓜", "hybrid")
    with pytest.raises(ValueError):
        make().search("地瓜", "unknown", "embeddinggemma")


def test_hybrid_fuses_bm25_and_vector():
    r = make(vector_rank={"雞蛋蛋白質": ["d:0", "c:0"]})
    result = r.search("雞蛋蛋白質", "hybrid", "embeddinggemma")
    assert result.hits[0].chunk_id == "d:0"
    assert result.hits[0].score == pytest.approx(2 / 61, abs=1e-4)


def test_rerank_pool_caps_chunks_per_article():
    r = make(rerank={"地瓜葉含鈣與維生素K。": 0.9})
    result = r.search("地瓜好處", "hybrid_rerank", "embeddinggemma")
    sent = r.reranker().calls[0]
    assert sum(t.startswith("地瓜好處多") for t in sent) == PER_ARTICLE
    assert result.hits[0].article_key == "hy:2" and result.hits[0].score == 0.9


def test_entity_boost_uses_food_itself_not_longer_food_names():
    # 重排把「地瓜葉」文章排第一；問「地瓜」時，標題只有「地瓜葉」的文章不加標題分
    r = make(rerank={"地瓜葉含鈣與維生素K。": 0.80, "地瓜富含膳食纖維，有助腸道健康。": 0.70})
    result = r.search("吃地瓜有什麼好處", "entity_boost", "embeddinggemma")
    assert result.extras["entities"] == ["地瓜"]
    assert [h.article_key for h in result.hits[:2]] == ["hy:1", "hy:2"]
    assert result.hits[0].score == pytest.approx(0.85) and result.hits[1].score == pytest.approx(0.80)
    assert r.entities("地瓜葉可以天天吃嗎") == ["地瓜葉"]


def test_entity_boost_without_food_equals_rerank():
    r = make(rerank={"雞蛋是優質蛋白質來源。": 0.6})
    a = r.search("增加蛋白質", "entity_boost", "embeddinggemma")
    b = r.search("增加蛋白質", "hybrid_rerank", "embeddinggemma")
    assert a.extras["entities"] == [] and [h.chunk_id for h in a.hits] == [h.chunk_id for h in b.hits]


def test_multi_query_rewrites_cached_and_reranked_with_original():
    chat = FakeChat(json.dumps({"queries": ["痛風患者能吃豆腐嗎", "痛風患者能吃豆腐嗎", " 高尿酸 豆製品 ", 3]},
                               ensure_ascii=False))
    r = make(rerank={"痛風患者可適量吃豆腐。": 0.95}, chat=chat)
    first = r.search("痛風可以吃豆腐嗎", "multi_query", "embeddinggemma")
    r.search("痛風可以吃豆腐嗎", "multi_query", "embeddinggemma")
    assert first.extras["rewrites"] == ["痛風患者能吃豆腐嗎", "高尿酸 豆製品"]  # 去重、去空白、忽略非字串
    assert chat.calls == 1  # 兩個嵌入模型共用同一組改寫，比較才公平
    assert first.hits[0].chunk_id == "c:0" and first.timings["rewrite"] >= 0


def test_multi_query_falls_back_when_llm_unavailable_or_bad_json():
    r = make(chat=FakeChat(ProviderUnavailable("LLM_FAILED")))
    result = r.search("地瓜", "multi_query", "embeddinggemma")
    assert result.extras["rewrite_error"] == "LLM_FAILED" and result.extras["rewrites"] == [] and result.hits
    assert make(chat=FakeChat("不是 JSON")).search("地瓜", "multi_query", "embeddinggemma").extras["rewrites"] == []


def test_result_dict_has_label_timings_and_hits():
    data = make().search("地瓜", "vector", "embeddinggemma").to_dict()
    assert data["method"] == "vector@embeddinggemma" and "total" in data["timings"]
    assert {"title", "url", "source_name", "text", "rank"} <= set(data["hits"][0])


def test_ollama_chat_payload_and_errors():
    import httpx

    from assistant_rag.providers import OllamaChat
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"queries": []}'}})
    chat = OllamaChat("qwen3.5:9b-q4_K_M", "http://o", transport=httpx.MockTransport(handler))
    assert chat.complete([{"role": "user", "content": "q"}], json_mode=True) == '{"queries": []}'
    assert seen[0]["think"] is False and seen[0]["format"] == "json" and seen[0]["stream"] is False
    bad = OllamaChat("m", "http://o", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderUnavailable) as err:
        bad.complete([])
    assert err.value.code == "LLM_BAD_RESPONSE"
