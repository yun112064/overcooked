"""T11 知識 RAG 測試工具：團隊電腦的知識資料庫在測試時是獨立的 <測試庫>_knowledge。

知識庫與雲端業務庫分開（Spec：知識在團隊電腦），因此不放進雲端 Alembic 鏈；
測試以 knowledge_rag/schema.sql 重建。所有知識內容皆為合成資料，不代表真實來源（G07 未通過）。
"""
import re

import psycopg

from db_support import TEST_DATABASE_URL

DIMENSIONS = 1536
EMBEDDING_MODEL = "text-embedding-3-small"
TOPICS = ("鈣", "維生素", "纖維")


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def knowledge_url() -> str:
    base = _libpq(TEST_DATABASE_URL)
    return re.sub(r"/([^/?]+)(\?|$)", lambda m: f"/{m.group(1)}_knowledge{m.group(2)}", base, count=1)


def fresh_knowledge_db() -> str:
    """建立（若不存在）並清空測試知識庫，回傳連線字串。"""
    url = knowledge_url()
    name = url.rsplit("/", 1)[1].split("?")[0]
    with psycopg.connect(_libpq(TEST_DATABASE_URL), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{name}"')
    from knowledge_rag import store
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS knowledge_embeddings, knowledge_indexes, knowledge_records, source_articles CASCADE")
    store.apply_schema(url)
    return url


def fake_vector(text: str) -> list[float]:
    """合成向量：依主題詞決定主要維度，讓距離排序可人工預期。"""
    vec = [0.001] * DIMENSIONS
    for axis, topic in enumerate(TOPICS):
        if topic in text:
            vec[axis] = 1.0
    return vec


class FakeEmbedder:
    """取代 OpenAI 向量化：回傳 (向量, usage)，與真實轉接器相同形狀。"""

    model = EMBEDDING_MODEL

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [fake_vector(t) for t in texts], {"input_tokens": sum(len(t) for t in texts), "output_tokens": 0}


def record(knowledge_id, ingredient_id, excerpt, version="v1"):
    return {"knowledge_id": knowledge_id, "ingredient_id": ingredient_id, "excerpt": excerpt,
            "source_url": f"https://example.invalid/knowledge/{knowledge_id}", "source_version": version,
            "rights": "合成測試資料，非真實來源", "conditions": "一般成人；合成情境"}
