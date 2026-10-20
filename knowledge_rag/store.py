"""T11 團隊電腦營養知識庫（R12）：PostgreSQL＋pgvector 的知識版本、審核、索引與檢索。

語意沿用 T04 nutrition_model/knowledge_service.py 的契約：
- 匯入一律 pending；審核後不可修改；同 knowledge_id 匯入新版本即取代舊版（舊版留在歷史）。
- 只有「現行版本＋已審核＋在 active 索引內」的知識可被檢索。
- 先以最終配方的 FOOD 編號限定，再以向量距離排序；這不是食譜檢索，也不選菜。
- 庫或索引無法使用時拋出 KnowledgeStoreError，絕不回傳成正常的零筆結果。

向量由呼叫端提供（雲端 worker 經 T10 額度預留後呼叫 OpenAI），本模組不呼叫付費服務。
"""
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

DIMENSIONS = 1536
FIELDS = ("knowledge_id", "ingredient_id", "excerpt", "source_url", "source_version", "rights", "conditions")
FOOD_ID = re.compile(r"^FOOD\d{6}$")
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class KnowledgeStoreError(Exception):
    """知識服務不可用（STORE_UNAVAILABLE／NO_ACTIVE_INDEX／EMBEDDING_MODEL_MISMATCH）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def apply_schema(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def _vector(values) -> str:
    if (not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)):
        raise ValueError(f"Embedding must be {DIMENSIONS} numbers")
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _embedding_text(row: dict) -> str:
    return f"{row['excerpt']}\n適用條件：{row['conditions']}"


class KnowledgeStore:
    def __init__(self, url: str):
        self.url = url

    def _connect(self):
        try:
            return psycopg.connect(self.url, row_factory=dict_row)
        except psycopg.Error:
            raise KnowledgeStoreError("STORE_UNAVAILABLE") from None

    # --- 匯入與審核（管理工具用，不對雲端開放） ---

    def import_record(self, record: dict) -> dict:
        if not isinstance(record, dict):
            raise ValueError("Knowledge record must be an object")
        for key in FIELDS:
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Missing knowledge {key}")
        if not FOOD_ID.match(record["ingredient_id"]):
            raise ValueError("ingredient_id must be a FOOD standard ingredient ID")
        parsed = urlparse(record["source_url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Invalid source URL")
        with self._connect() as conn:
            try:
                return conn.execute(
                    "INSERT INTO knowledge_records (knowledge_id, source_version, ingredient_id, excerpt, source_url, "
                    "rights, conditions) VALUES (%(knowledge_id)s, %(source_version)s, %(ingredient_id)s, %(excerpt)s, "
                    "%(source_url)s, %(rights)s, %(conditions)s) RETURNING knowledge_id, source_version, status",
                    {k: record[k] for k in FIELDS}).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ValueError("Existing versions are immutable; import a new source_version") from None

    def review(self, knowledge_id: str, source_version: str, *, reviewer: str, approved: bool, note: str) -> dict:
        if not all(isinstance(v, str) and v.strip() for v in (reviewer, note)) or not isinstance(approved, bool):
            raise ValueError("Reviewer, decision and citation-check note required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM knowledge_records WHERE knowledge_id = %s AND source_version = %s FOR UPDATE",
                (knowledge_id, source_version)).fetchone()
            if row is None:
                raise ValueError("Knowledge version not found")
            if row["status"] != "pending":
                raise ValueError("A reviewed version is immutable")
            return conn.execute(
                "UPDATE knowledge_records SET status = %s, reviewer = %s, reviewed_at = now(), review_note = %s "
                "WHERE knowledge_id = %s AND source_version = %s RETURNING knowledge_id, source_version, status",
                ("approved" if approved else "rejected", reviewer, note, knowledge_id, source_version)).fetchone()

    # --- 索引 ---

    def build_index(self, index_version: str, embedding_model: str,
                    embed: Callable[[list[str]], list[list[float]]], *, batch_size: int = 50) -> dict:
        """為現行且已審核的知識建立新索引版本並啟用；embed 由呼叫端負責額度預留與結算。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
                "ORDER BY knowledge_id, seq DESC) c WHERE status = 'approved' ORDER BY knowledge_id").fetchall()
            conn.execute("INSERT INTO knowledge_indexes (index_version, embedding_model, dimensions, status) "
                         "VALUES (%s, %s, %s, 'building')", (index_version, embedding_model, DIMENSIONS))
            conn.commit()
        # 付費向量化在資料庫交易外進行
        vectors: list[str] = []
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            result = embed([_embedding_text(r) for r in batch])
            if len(result) != len(batch):
                raise ValueError("Embedding count does not match records")
            vectors += [_vector(v) for v in result]
        with self._connect() as conn:
            for row, vec in zip(rows, vectors):
                conn.execute("INSERT INTO knowledge_embeddings (index_version, knowledge_id, source_version, embedding) "
                             "VALUES (%s, %s, %s, %s::vector)",
                             (index_version, row["knowledge_id"], row["source_version"], vec))
            conn.execute("UPDATE knowledge_indexes SET status = 'retired' WHERE status = 'active'")
            conn.execute("UPDATE knowledge_indexes SET status = 'active', activated_at = now(), record_count = %s "
                         "WHERE index_version = %s", (len(rows), index_version))
        return {"index_version": index_version, "embedding_model": embedding_model, "records": len(rows)}

    def active_index(self) -> dict | None:
        try:
            with self._connect() as conn:
                return conn.execute("SELECT index_version, embedding_model, dimensions, record_count "
                                    "FROM knowledge_indexes WHERE status = 'active'").fetchone()
        except psycopg.Error:
            raise KnowledgeStoreError("STORE_UNAVAILABLE") from None

    # --- 檢索 ---

    def search(self, food_ids: list[str], query_embedding: list[float], embedding_model: str, *,
               limit_per_ingredient: int = 3) -> dict:
        if (not isinstance(food_ids, (list, tuple)) or not food_ids
                or not all(isinstance(i, str) and FOOD_ID.match(i) for i in food_ids)):
            raise ValueError("food_ids must be FOOD standard ingredient IDs")
        if not isinstance(limit_per_ingredient, int) or not 1 <= limit_per_ingredient <= 10:
            raise ValueError("limit_per_ingredient must be 1-10")
        query = _vector(query_embedding)
        wanted = sorted(set(food_ids))
        try:
            with self._connect() as conn:
                index = conn.execute("SELECT index_version, embedding_model FROM knowledge_indexes "
                                     "WHERE status = 'active'").fetchone()
                if index is None:
                    raise KnowledgeStoreError("NO_ACTIVE_INDEX")
                if index["embedding_model"] != embedding_model:
                    raise KnowledgeStoreError("EMBEDDING_MODEL_MISMATCH")
                rows = conn.execute("""
                    WITH current AS (
                        SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records ORDER BY knowledge_id, seq DESC
                    ), ranked AS (
                        SELECT c.*, e.embedding <=> %(q)s::vector AS distance,
                               row_number() OVER (PARTITION BY c.ingredient_id
                                                  ORDER BY e.embedding <=> %(q)s::vector, c.knowledge_id) AS rn
                        FROM current c
                        JOIN knowledge_embeddings e
                          ON e.knowledge_id = c.knowledge_id AND e.source_version = c.source_version
                         AND e.index_version = %(idx)s
                        WHERE c.ingredient_id = ANY(%(ids)s) AND c.status = 'approved'
                    )
                    SELECT knowledge_id, source_version, ingredient_id, excerpt, source_url, rights, conditions,
                           status, reviewer, reviewed_at, distance
                    FROM ranked WHERE rn <= %(k)s ORDER BY ingredient_id, distance, knowledge_id
                """, {"q": query, "idx": index["index_version"], "ids": wanted, "k": limit_per_ingredient}).fetchall()
        except psycopg.Error:
            raise KnowledgeStoreError("STORE_UNAVAILABLE") from None
        records = [dict(r, reviewed_at=r["reviewed_at"].isoformat(), distance=round(float(r["distance"]), 6))
                   for r in rows]
        found = {r["ingredient_id"] for r in records}
        return {"status": "available" if records else "not_found", "records": records,
                "by_ingredient": {i: "available" if i in found else "not_found" for i in wanted},
                "index_version": index["index_version"], "embedding_model": index["embedding_model"]}
