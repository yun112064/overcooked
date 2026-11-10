"""R14 步驟 2：分段、斷詞、BM25、模型介面與資料庫名稱限制。合成資料、假 HTTP，不連網、不連資料庫。"""
import json

import httpx
import pytest

from assistant_rag import index, store
from assistant_rag.bm25 import BM25
from assistant_rag.chunking import OVERLAP, TARGET, chunk_article, dedupe, sentences
from assistant_rag.providers import EMBEDDERS, OllamaEmbedder, ProviderUnavailable, TeiReranker
from assistant_rag.segment import Tokenizer, food_terms


def record(sections, **extra):
    r = {"source": "helloyishi", "article_id": "1", "title": "地瓜功效", "content_hash": "h1",
         "url": "https://x/1", "sections": sections}
    r.update(extra)
    return r


def sec(path, paragraphs):
    return {"heading": path[-1] if path else None, "heading_path": path, "paragraphs": paragraphs}


def test_chunks_respect_target_overlap_and_prefix():
    long = "".join(f"第{i}句地瓜含有膳食纖維與鉀，有助維持腸道健康。" for i in range(40))
    chunks = chunk_article(record([sec([], ["地瓜是根莖類。"]), sec(["地瓜的好處"], [long])]))
    assert len(chunks) > 3
    assert all(c.char_count <= TARGET for c in chunks[:-1]) and chunks[-1].char_count <= TARGET + OVERLAP
    # 相鄰片段有重疊句
    for a, b in zip(chunks[1:], chunks[2:]):
        assert sentences(b.text)[0] in a.text and len(sentences(b.text)[0]) <= OVERLAP
    assert chunks[1].embed_text.startswith("地瓜功效｜地瓜的好處\n")
    assert [c.chunk_id for c in chunks[:2]] == ["helloyishi:1:0", "helloyishi:1:1"]


def test_small_sections_merge_h3_marker_and_toc_removed():
    chunks = chunk_article(record([
        sec([], ["文章開頭介紹地瓜。", "地瓜的好處", "地瓜怎麼吃"]),  # 後兩行是目錄
        sec(["地瓜的好處"], ["富含膳食纖維。"]),
        sec(["地瓜的好處", "好處1. 補充纖維"], ["膳食纖維", "鉀"]),
        sec(["地瓜怎麼吃"], ["建議連皮蒸熟。"]),
    ]))
    assert len(chunks) == 1  # 內容太少，全部併成一片
    text = chunks[0].text
    assert "▸好處1. 補充纖維" in text and "地瓜怎麼吃" not in text.split("\n")[:3]
    assert text.startswith("文章開頭介紹地瓜。\n富含膳食纖維。")


def test_new_h2_starts_new_chunk_when_current_is_large_enough():
    body = "地瓜含有豐富的膳食纖維，" * 15 + "。"
    chunks = chunk_article(record([sec(["好處"], [body]), sec(["吃法"], ["蒸熟後連皮吃。"])]))
    assert [c.heading_path for c in chunks] == [["好處"]] or chunks[-1].heading_path == ["吃法"]
    assert chunks[0].heading_path == ["好處"]


def test_dedupe_keeps_first_and_reports_duplicates():
    a = record([sec([], ["a"])], article_id="1", content_hash="same")
    b = record([sec([], ["a"])], article_id="2", content_hash="same")
    c = record([sec([], ["c"])], article_id="3", content_hash="other")
    kept, dups = dedupe([a, b, c])
    assert [r["article_id"] for r in kept] == ["1", "3"] and dups == {"helloyishi:2": "helloyishi:1"}


def test_tokenizer_food_dictionary_and_stopwords():
    tok = Tokenizer(["地瓜葉", "膳食纖維", "蛋白質"])
    tokens = tok.tokens("吃地瓜葉可以補充膳食纖維和蛋白質嗎？")
    assert {"地瓜葉", "膳食纖維", "蛋白質"} <= set(tokens)
    assert "可以" not in tokens and "嗎" not in tokens and "？" not in tokens
    assert tok.version.startswith("assistant-jieba-v3+dict-")
    # 問句用語去掉；「吃豆腐」「喝咖啡」拆成食物
    assert tok.tokens("痛風可以吃豆腐嗎") == ["痛風", "豆腐"]
    assert tok.tokens("孕婦可以喝咖啡嗎") == ["孕婦", "咖啡"]
    assert "吃什麼" not in tok.tokens("我想增加蛋白質可以吃什麼")
    assert "便秘" in tok.tokens("改善便祕要吃什麽")


def test_food_terms_from_project_masters():
    terms = food_terms()
    assert "大麥仁" in terms and "醬油" in terms
    assert all(2 <= len(t) <= 8 for t in terms)


def test_bm25_ranks_matching_documents():
    bm = BM25(["a", "b", "c"], [["地瓜", "纖維", "地瓜"], ["雞蛋", "蛋白質"], ["地瓜", "熱量"]])
    ranked = bm.search(["地瓜", "纖維"], k=3)
    assert [cid for cid, _ in ranked] == ["a", "c"]
    assert bm.search(["不存在"]) == []


def ollama_transport(dims, seen):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "embeddinggemma:latest", "digest": "d" * 64},
                                                        {"name": "bge-m3:latest", "digest": "b" * 64}]})
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"embeddings": [[0.1] * dims for _ in body["input"]]})
    return httpx.MockTransport(handler)


def test_embedder_templates_and_dimension_check():
    seen = []
    gemma = OllamaEmbedder(EMBEDDERS["embeddinggemma"], "http://o", transport=ollama_transport(768, seen))
    assert gemma.fingerprint() == "d" * 64
    gemma.documents(["內文"], ["標題"])
    gemma.query("地瓜")
    assert seen[0]["input"] == ["title: 標題 | text: 內文"]
    assert seen[1]["input"] == ["task: search result | query: 地瓜"]
    bge = OllamaEmbedder(EMBEDDERS["bge-m3"], "http://o", transport=ollama_transport(768, seen))
    with pytest.raises(ProviderUnavailable) as err:
        bge.query("地瓜")  # 回傳 768 維但 bge-m3 應為 1024
    assert err.value.code == "EMBED_BAD_RESPONSE"


def test_embedder_not_installed_and_service_down():
    missing = OllamaEmbedder(EMBEDDERS["bge-m3"], "http://o", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": []})))
    with pytest.raises(ProviderUnavailable) as err:
        missing.fingerprint()
    assert err.value.code == "EMBED_MODEL_NOT_INSTALLED"

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(ProviderUnavailable) as err:
        OllamaEmbedder(EMBEDDERS["bge-m3"], "http://o", transport=httpx.MockTransport(down)).fingerprint()
    assert err.value.code == "EMBED_SERVICE_DOWN"


def test_reranker_batches_and_restores_order():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(len(body["texts"]))
        n = len(body["texts"])
        return httpx.Response(200, json=[{"index": i, "score": (n - i) / 10} for i in reversed(range(n))])
    scores = TeiReranker("http://r", transport=httpx.MockTransport(handler), batch=2).rerank("q", ["a", "b", "c"])
    assert calls == [2, 1] and scores == [0.2, 0.1, 0.1]


def test_database_name_must_be_assistant(monkeypatch):
    for url in ("postgresql://u:p@h/meal_planner_1102_v3", "postgresql://u:p@h/overcooked_knowledge_dev",
                "postgresql://u:p@h/"):
        with pytest.raises(RuntimeError):
            store.check_url(url)
    store.check_url("postgresql://u:p@h/overcooked_assistant")
    monkeypatch.setenv("ASSISTANT_DATABASE_URL", "postgresql+psycopg://u:p@h:1/overcooked_assistant_test")
    assert store.database_url() == "postgresql://u:p@h:1/overcooked_assistant_test"


def test_vector_literal_and_unknown_table():
    assert store.vector_literal([0.5, -1.0, 1e-9]) == "[0.5,-1,1e-09]"
    with pytest.raises(ValueError):
        store.embed_table("openai")


def test_prepare_dedupes_tokenizes_and_versions_stably():
    a = record([sec(["好處"], ["地瓜富含膳食纖維。"])], article_id="1", content_hash="x", source_name="Hello醫師",
               char_count=9)
    b = dict(a, article_id="2")
    tok = Tokenizer(["膳食纖維"])
    meta, articles, chunks = index.prepare([a, b], tok)
    assert meta["duplicate_count"] == 1 and articles[0]["duplicates"] == ["helloyishi:2"]
    assert "膳食纖維" in chunks[0].tokens and meta["stats"]["per_source"]["helloyishi"] == {"articles": 1, "chunks": 1}
    assert index.version_name(meta) == index.version_name(index.prepare([a, b], tok)[0])


def test_exclusions_apply_only_when_approved(tmp_path):
    path = tmp_path / "exclusions.json"
    data = {"status": "pending_review", "articles": [{"article_key": "helloyishi:2", "title": "x", "reason": "醫美"}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert index.load_exclusions(path) == (frozenset(), "pending_review")
    data["status"] = "approved"
    path.write_text(json.dumps(data), encoding="utf-8")
    keys, status = index.load_exclusions(path)
    assert keys == {"helloyishi:2"} and status == "approved"
    assert index.load_exclusions(tmp_path / "none.json") == (frozenset(), "missing")
    a = record([sec(["好處"], ["地瓜富含膳食纖維。"])], article_id="1", content_hash="x", source_name="Hello醫師", char_count=9)
    b = record([sec(["療程"], ["電波拉皮。"])], article_id="2", content_hash="y", source_name="Hello醫師", char_count=5)
    tok = Tokenizer(["膳食纖維"])
    meta, articles, _ = index.prepare([a, b], tok, keys)
    assert [x["article_key"] for x in articles] == ["helloyishi:1"] and meta["excluded_count"] == 1
    assert index.version_name(meta) != index.version_name(index.prepare([a, b], tok)[0])


def test_repository_exclusion_list_well_formed():
    data = json.loads(index.EXCLUSIONS.read_text(encoding="utf-8"))
    keys = [a["article_key"] for a in data["articles"]]
    assert data["status"] in {"pending_review", "approved"} and len(keys) == len(set(keys))
    assert all(k.startswith(("healthnews:", "helloyishi:")) and a["reason"] and a["title"]
               for k, a in zip(keys, data["articles"]))
