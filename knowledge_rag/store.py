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
# G07 外部來源的選填欄位（合成資料可省略）；顯示時必須帶出處
SOURCE_FIELDS = ("source_name", "title", "published_at", "fetched_at", "content_hash", "authorization_ref",
                 "article_id", "content_provider", "license_status")
# 轉載內容授權（pre-review #8）
LICENSE_UNCONFIRMED, LICENSE_CONFIRMED = "third_party_unconfirmed", "third_party_confirmed"
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
        extra = {k: record[k] for k in SOURCE_FIELDS if record.get(k) is not None}
        if extra.get("source_name") and not str(extra.get("authorization_ref") or "").strip():
            raise ValueError("External-source knowledge requires an authorization_ref")
        if extra.get("content_provider") and extra.get("license_status") != LICENSE_CONFIRMED:
            extra["license_status"] = LICENSE_UNCONFIRMED  # 轉載內容一律先擱置，待審核者確認授權
        columns = [*FIELDS, *extra]
        with self._connect() as conn:
            try:
                return conn.execute(
                    f"INSERT INTO knowledge_records ({', '.join(columns)}) "
                    f"VALUES ({', '.join(f'%({c})s' for c in columns)}) RETURNING knowledge_id, source_version, status",
                    {**{k: record[k] for k in FIELDS}, **extra}).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ValueError("Existing versions are immutable; import a new source_version") from None

    def review(self, knowledge_id: str, source_version: str, *, reviewer: str, approved: bool, note: str,
               confirm_third_party_license: bool = False) -> dict:
        """核准／退回一個版本。轉載內容（license_status=third_party_unconfirmed）核准時必須明確確認
        授權涵蓋轉載內容（confirm_third_party_license），確認會寫入審核紀錄；退回不需要。"""
        if not all(isinstance(v, str) and v.strip() for v in (reviewer, note)) or not isinstance(approved, bool):
            raise ValueError("Reviewer, decision and citation-check note required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, license_status, content_provider FROM knowledge_records "
                "WHERE knowledge_id = %s AND source_version = %s FOR UPDATE",
                (knowledge_id, source_version)).fetchone()
            if row is None:
                raise ValueError("Knowledge version not found")
            if row["status"] != "pending":
                raise ValueError("A reviewed version is immutable")
            license_status = row["license_status"]
            if approved and license_status == LICENSE_UNCONFIRMED:
                if not confirm_third_party_license:
                    raise ValueError(f"Reprinted third-party content ({row['content_provider']}) is held: confirm the "
                                     "written permission covers it, then approve with --confirm-third-party-license")
                license_status = LICENSE_CONFIRMED
                note = f"[已確認轉載授權涵蓋原始供稿：{row['content_provider']}] {note}"
            return conn.execute(
                "UPDATE knowledge_records SET status = %s, reviewer = %s, reviewed_at = now(), review_note = %s, "
                "license_status = %s WHERE knowledge_id = %s AND source_version = %s "
                "RETURNING knowledge_id, source_version, status, license_status",
                ("approved" if approved else "rejected", reviewer, note, license_status, knowledge_id,
                 source_version)).fetchone()

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
                           status, reviewer, reviewed_at, distance, source_name, title, published_at,
                           content_provider
                    FROM ranked WHERE rn <= %(k)s ORDER BY ingredient_id, distance, knowledge_id
                """, {"q": query, "idx": index["index_version"], "ids": wanted, "k": limit_per_ingredient}).fetchall()
        except psycopg.Error:
            raise KnowledgeStoreError("STORE_UNAVAILABLE") from None
        records = [dict(r, reviewed_at=r["reviewed_at"].isoformat(), distance=round(float(r["distance"]), 6),
                        published_at=r["published_at"].isoformat() if r["published_at"] else None)
                   for r in rows]
        found = {r["ingredient_id"] for r in records}
        return {"status": "available" if records else "not_found", "records": records,
                "by_ingredient": {i: "available" if i in found else "not_found" for i in wanted},
                "index_version": index["index_version"], "embedding_model": index["embedding_model"]}

    # --- 管理查詢（管理指令用） ---

    def list_records(self, *, status: str | None = None, current_only: bool = True, limit: int = 1000) -> list[dict]:
        sql = ("SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
               "ORDER BY knowledge_id, seq DESC) c" if current_only else "SELECT * FROM knowledge_records c")
        with self._connect() as conn:
            return conn.execute(sql + " WHERE (%(s)s::text IS NULL OR c.status = %(s)s) ORDER BY c.seq LIMIT %(n)s",
                                {"s": status, "n": limit}).fetchall()

    def get_record(self, knowledge_id: str, source_version: str | None = None) -> dict | None:
        """指定版本，或省略時取現行（最新匯入）版本。"""
        with self._connect() as conn:
            if source_version is None:
                return conn.execute("SELECT * FROM knowledge_records WHERE knowledge_id = %s ORDER BY seq DESC LIMIT 1",
                                    (knowledge_id,)).fetchone()
            return conn.execute("SELECT * FROM knowledge_records WHERE knowledge_id = %s AND source_version = %s",
                                (knowledge_id, source_version)).fetchone()

    def list_versions(self, knowledge_id: str) -> list[dict]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM knowledge_records WHERE knowledge_id = %s ORDER BY seq",
                                (knowledge_id,)).fetchall()

    def status_summary(self) -> dict:
        """現行版本依狀態計數、被待審新版本暫停的已審核知識、active 索引與抓取統計。"""
        with self._connect() as conn:
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) status FROM knowledge_records "
                "ORDER BY knowledge_id, seq DESC) c GROUP BY status")}
            superseded = conn.execute(
                "SELECT count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) knowledge_id, status FROM "
                "knowledge_records ORDER BY knowledge_id, seq DESC) c WHERE c.status = 'pending' AND EXISTS ("
                "SELECT 1 FROM knowledge_records o WHERE o.knowledge_id = c.knowledge_id AND o.status = 'approved')"
            ).fetchone()["n"]
            articles = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, count(*) AS n FROM source_articles GROUP BY status")}
            index = conn.execute("SELECT index_version, embedding_model, record_count, activated_at "
                                 "FROM knowledge_indexes WHERE status = 'active'").fetchone()
            holds = conn.execute(
                "SELECT count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) status, license_status FROM "
                "knowledge_records ORDER BY knowledge_id, seq DESC) c WHERE c.status = 'pending' "
                "AND c.license_status = %s", (LICENSE_UNCONFIRMED,)).fetchone()["n"]
        return {"records": {k: counts.get(k, 0) for k in ("pending", "approved", "rejected")},
                "approved_awaiting_new_version_review": superseded, "articles": articles, "active_index": index,
                "license_holds": holds}

    def ingredient_coverage(self, food_ids) -> dict:
        food_ids = list(food_ids)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ingredient_id, status, count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) * FROM "
                "knowledge_records ORDER BY knowledge_id, seq DESC) c WHERE ingredient_id = ANY(%s) "
                "GROUP BY ingredient_id, status", (food_ids,)).fetchall()
        out = {i: {"pending": 0, "approved": 0} for i in food_ids}
        for r in rows:
            if r["status"] in ("pending", "approved"):
                out[r["ingredient_id"]][r["status"]] = r["n"]
        return out

    # --- 來源文章抓取狀態 ---

    def known_article_ids(self, source_name: str) -> set[str]:
        with self._connect() as conn:
            return {r["article_id"] for r in conn.execute(
                "SELECT article_id FROM source_articles WHERE source_name = %s", (source_name,))}

    def save_article(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO source_articles (source_name, article_id, url, title, published_at, modified_at,
                                             content_hash, status, matched_food_ids, last_changed_at)
                VALUES (%(source_name)s, %(article_id)s, %(url)s, %(title)s, %(published_at)s, %(modified_at)s,
                        %(content_hash)s, %(status)s, %(matched_food_ids)s, now())
                ON CONFLICT (source_name, article_id) DO UPDATE SET
                    url = EXCLUDED.url, title = EXCLUDED.title, published_at = EXCLUDED.published_at,
                    modified_at = EXCLUDED.modified_at, status = EXCLUDED.status,
                    matched_food_ids = EXCLUDED.matched_food_ids, last_fetched_at = now(),
                    last_changed_at = CASE WHEN source_articles.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                                           THEN now() ELSE source_articles.last_changed_at END,
                    content_hash = EXCLUDED.content_hash
            """, {"title": None, "published_at": None, "modified_at": None, "content_hash": None,
                  "matched_food_ids": [], **row})
