-- 飲食客服 RAG 獨立資料庫（overcooked_assistant*）。與業務庫、T11 知識庫分開。
-- 一個 index version = 一份去重後的語料＋分段＋斷詞；每個嵌入模型一張向量表（維度不同）。
-- 語料約數千片，向量檢索用精確搜尋（不建 HNSW），比較各方法時不受近似誤差影響。
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS index_versions (
    version            text PRIMARY KEY,
    created_at         timestamptz NOT NULL DEFAULT now(),
    status             text NOT NULL CHECK (status IN ('building', 'ready', 'retired')),
    active             boolean NOT NULL DEFAULT false,
    parser_version     text NOT NULL,
    chunker_version    text NOT NULL,
    tokenizer_version  text NOT NULL,
    corpus_hash        text NOT NULL,
    article_count      integer NOT NULL,
    duplicate_count    integer NOT NULL,
    chunk_count        integer NOT NULL,
    stats              jsonb NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS index_versions_one_active ON index_versions (active) WHERE active;

CREATE TABLE IF NOT EXISTS articles (
    version          text NOT NULL REFERENCES index_versions ON DELETE CASCADE,
    article_key      text NOT NULL,
    source           text NOT NULL,
    source_name      text NOT NULL,
    article_id       text NOT NULL,
    url              text NOT NULL,
    title            text NOT NULL,
    published_at     timestamptz,
    modified_at      timestamptz,
    author           text,
    reviewers        jsonb NOT NULL DEFAULT '{}',
    content_provider text,
    category         text,
    subcategory      text,
    keywords         text[] NOT NULL DEFAULT '{}',
    description      text,
    content_hash     text NOT NULL,
    char_count       integer NOT NULL,
    duplicates       text[] NOT NULL DEFAULT '{}',  -- 內容相同而未收錄的文章 key
    PRIMARY KEY (version, article_key)
);

CREATE TABLE IF NOT EXISTS chunks (
    version      text NOT NULL,
    chunk_id     text NOT NULL,
    article_key  text NOT NULL,
    position     integer NOT NULL,
    heading_path text[] NOT NULL DEFAULT '{}',
    text         text NOT NULL,
    embed_text   text NOT NULL,
    char_count   integer NOT NULL,
    tokens       text[] NOT NULL,
    PRIMARY KEY (version, chunk_id),
    FOREIGN KEY (version, article_key) REFERENCES articles ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embedding_runs (
    version      text NOT NULL REFERENCES index_versions ON DELETE CASCADE,
    embedder     text NOT NULL,
    model        text NOT NULL,
    digest       text NOT NULL,
    dimensions   integer NOT NULL,
    template     text NOT NULL,
    status       text NOT NULL CHECK (status IN ('building', 'ready')),
    chunk_count  integer NOT NULL DEFAULT 0,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    seconds      double precision NOT NULL DEFAULT 0,
    PRIMARY KEY (version, embedder)
);

CREATE TABLE IF NOT EXISTS emb_bge_m3 (
    version   text NOT NULL,
    chunk_id  text NOT NULL,
    embedding vector(1024) NOT NULL,
    PRIMARY KEY (version, chunk_id),
    FOREIGN KEY (version, chunk_id) REFERENCES chunks ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emb_embeddinggemma (
    version   text NOT NULL,
    chunk_id  text NOT NULL,
    embedding vector(768) NOT NULL,
    PRIMARY KEY (version, chunk_id),
    FOREIGN KEY (version, chunk_id) REFERENCES chunks ON DELETE CASCADE
);
