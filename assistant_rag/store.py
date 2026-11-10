"""飲食客服獨立資料庫（pgvector）。只接受資料庫名稱以 overcooked_assistant 開頭的連線，避免誤寫業務庫或 T11 知識庫。

連線：ASSISTANT_DATABASE_URL；未設定時讀 Data Root 的 db.env（密碼不進 Git）。
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from assistant_rag.config import data_dir

SCHEMA = Path(__file__).with_name("schema.sql")
DB_PREFIX = "overcooked_assistant"
EMBED_TABLES = {"bge-m3": "emb_bge_m3", "embeddinggemma": "emb_embeddinggemma"}


def database_url(explicit: str | None = None) -> str:
    url = explicit or os.getenv("ASSISTANT_DATABASE_URL")
    if not url:
        env_file = data_dir() / "db.env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("ASSISTANT_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        raise RuntimeError("請設定 ASSISTANT_DATABASE_URL（飲食客服專用資料庫）")
    check_url(url)
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def check_url(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    if not name.startswith(DB_PREFIX):
        raise RuntimeError(f"資料庫名稱必須以 {DB_PREFIX} 開頭（目前：{name or '未指定'}），不可使用業務庫或知識庫")


def connect(url: str | None = None) -> psycopg.Connection:
    """autocommit：每批嵌入在自己的 conn.transaction() 內提交，中斷後已完成的批次保留、可接續。"""
    return psycopg.connect(database_url(url), autocommit=True)


def migrate(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


def embed_table(embedder: str) -> str:
    if embedder not in EMBED_TABLES:
        raise ValueError(f"未知的嵌入模型：{embedder}")
    return EMBED_TABLES[embedder]


def active_version(conn) -> str | None:
    row = conn.execute("SELECT version FROM index_versions WHERE active").fetchone()
    return row[0] if row else None


def insert_corpus(conn, version: str, meta: dict, articles: list[dict], chunks: list) -> None:
    """一次交易寫入 index version、文章與片段（status=building）。"""
    with conn.transaction():
        conn.execute(
            "INSERT INTO index_versions (version, status, parser_version, chunker_version, tokenizer_version,"
            " corpus_hash, article_count, duplicate_count, chunk_count, stats)"
            " VALUES (%s, 'building', %s, %s, %s, %s, %s, %s, %s, %s)",
            (version, meta["parser_version"], meta["chunker_version"], meta["tokenizer_version"],
             meta["corpus_hash"], len(articles), meta["duplicate_count"], len(chunks), json.dumps(meta["stats"])))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO articles (version, article_key, source, source_name, article_id, url, title,"
                " published_at, modified_at, author, reviewers, content_provider, category, subcategory,"
                " keywords, description, content_hash, char_count, duplicates)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(version, a["article_key"], a["source"], a["source_name"], a["article_id"], a["url"], a["title"],
                  a.get("published_at"), a.get("modified_at"), a.get("author"), json.dumps(a.get("reviewers") or {},
                  ensure_ascii=False), a.get("content_provider"), a.get("category"), a.get("subcategory"),
                  a.get("keywords") or [], a.get("description"), a["content_hash"], a["char_count"],
                  a.get("duplicates") or []) for a in articles])
            cur.executemany(
                "INSERT INTO chunks (version, chunk_id, article_key, position, heading_path, text, embed_text,"
                " char_count, tokens) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(version, c.chunk_id, c.article_key, c.position, c.heading_path, c.text, c.embed_text,
                  c.char_count, c.tokens) for c in chunks])


def missing_embeddings(conn, version: str, embedder: str) -> list[tuple[str, str, str]]:
    """尚未嵌入的片段：[(chunk_id, embed_text, 文章標題)]，依片段順序。"""
    table = embed_table(embedder)
    return conn.execute(
        f"SELECT c.chunk_id, c.embed_text, a.title FROM chunks c JOIN articles a USING (version, article_key)"
        f" LEFT JOIN {table} e ON e.version = c.version AND e.chunk_id = c.chunk_id"
        f" WHERE c.version = %s AND e.chunk_id IS NULL ORDER BY c.article_key, c.position", (version,)).fetchall()


def save_embeddings(conn, version: str, embedder: str, rows: list[tuple[str, list[float]]]) -> None:
    table = embed_table(embedder)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {table} (version, chunk_id, embedding) VALUES (%s, %s, %s::vector)",
                        [(version, cid, vector_literal(v)) for cid, v in rows])


def vector_search(conn, version: str, embedder: str, query_vector: list[float], k: int = 50):
    """精確 cosine 搜尋：[(chunk_id, 相似度)]。"""
    table = embed_table(embedder)
    return [(cid, float(sim)) for cid, sim in conn.execute(
        f"SELECT chunk_id, 1 - (embedding <=> %s::vector) AS sim FROM {table}"
        f" WHERE version = %s ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s",
        (vector_literal(query_vector), version, vector_literal(query_vector), k)).fetchall()]


def load_tokens(conn, version: str) -> tuple[list[str], list[list[str]]]:
    rows = conn.execute("SELECT chunk_id, tokens FROM chunks WHERE version = %s ORDER BY chunk_id",
                        (version,)).fetchall()
    return [r[0] for r in rows], [list(r[1]) for r in rows]


def chunk_details(conn, version: str, chunk_ids: list[str]) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT c.chunk_id, c.article_key, c.heading_path, c.text, a.title, a.url, a.source_name, a.published_at"
        " FROM chunks c JOIN articles a USING (version, article_key) WHERE c.version = %s AND c.chunk_id = ANY(%s)",
        (version, chunk_ids)).fetchall()
    keys = ("chunk_id", "article_key", "heading_path", "text", "title", "url", "source_name", "published_at")
    return {r[0]: dict(zip(keys, r)) for r in rows}


def article_meta(conn, version: str, keys: list[str]) -> dict[str, dict]:
    """出處資訊：作者、審稿、供稿者、日期。"""
    rows = conn.execute(
        "SELECT article_key, author, reviewers, content_provider, published_at, modified_at FROM articles"
        " WHERE version = %s AND article_key = ANY(%s)", (version, keys)).fetchall()
    return {r[0]: {"author": r[1], "reviewers": r[2] or {}, "content_provider": r[3],
                   "published_at": r[4].isoformat() if r[4] else None,
                   "modified_at": r[5].isoformat() if r[5] else None} for r in rows}
