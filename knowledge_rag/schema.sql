-- T11 團隊電腦營養知識庫（PostgreSQL＋pgvector）。與雲端業務庫分開，不在雲端 Alembic 鏈內。
-- 可重複執行；結構變更請發行新檔並在 docs/T11-recipe-retrieval-knowledge.md 記錄。
CREATE EXTENSION IF NOT EXISTS vector;

-- 每個知識版本一列；匯入一律 pending，審核後不可修改。同 knowledge_id 以最新匯入的版本為現行版本。
CREATE TABLE IF NOT EXISTS knowledge_records (
    seq            BIGSERIAL UNIQUE,
    knowledge_id   VARCHAR(64)  NOT NULL,
    source_version VARCHAR(100) NOT NULL,
    ingredient_id  VARCHAR(32)  NOT NULL,            -- 與 recipe_ingredients.food_id 相同的 FOOD 編號
    excerpt        TEXT         NOT NULL,
    source_url     TEXT         NOT NULL,
    rights         TEXT         NOT NULL,
    conditions     TEXT         NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'pending',
    reviewer       VARCHAR(100),
    reviewed_at    TIMESTAMPTZ,
    review_note    TEXT,
    imported_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_id, source_version),
    CONSTRAINT ck_knowledge_status CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT ck_knowledge_review CHECK (
        status = 'pending' OR (reviewer IS NOT NULL AND reviewed_at IS NOT NULL AND review_note IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_knowledge_records_ingredient ON knowledge_records (ingredient_id);

-- 索引版本：同時只有一個 active；檢索固定使用 active 版本與其向量模型。
CREATE TABLE IF NOT EXISTS knowledge_indexes (
    index_version   VARCHAR(64) PRIMARY KEY,
    embedding_model VARCHAR(100) NOT NULL,
    dimensions      INTEGER      NOT NULL,
    status          VARCHAR(20)  NOT NULL,
    record_count    INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    activated_at    TIMESTAMPTZ,
    CONSTRAINT ck_knowledge_index_status CHECK (status IN ('building', 'active', 'retired'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_single_active ON knowledge_indexes (status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    index_version  VARCHAR(64)  NOT NULL REFERENCES knowledge_indexes (index_version) ON DELETE CASCADE,
    knowledge_id   VARCHAR(64)  NOT NULL,
    source_version VARCHAR(100) NOT NULL,
    embedding      vector(1536) NOT NULL,
    PRIMARY KEY (index_version, knowledge_id, source_version),
    FOREIGN KEY (knowledge_id, source_version) REFERENCES knowledge_records (knowledge_id, source_version)
);

-- G07 外部來源（健康醫療網）欄位：顯示時必須帶出處（網站使用條款第 7 條不得移除著作權聲明）。
-- 合成／舊資料可為 NULL；來源轉接器匯入的紀錄一律填寫。授權紀錄欄位指向團隊保存的書面授權證據。
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS source_name       TEXT;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS title             TEXT;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS published_at      TIMESTAMPTZ;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS fetched_at        TIMESTAMPTZ;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS content_hash      CHAR(64);
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS authorization_ref TEXT;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS article_id        VARCHAR(32);
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS content_provider  TEXT;
CREATE INDEX IF NOT EXISTS ix_knowledge_records_article ON knowledge_records (source_name, article_id);
-- 轉載內容（文末「內容授權提供／X」）的授權狀態（pre-review #8）：third_party_unconfirmed＝待確認，不可核准；
-- 審核者明確確認授權涵蓋轉載內容後才可核准，改為 third_party_confirmed 並寫入審核紀錄。非轉載為 NULL。
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS license_status    VARCHAR(40);

-- 抓取狀態：每篇來源文章一列，用來跳過已抓過的文章與判斷內容是否改變（不存原文全文）。
CREATE TABLE IF NOT EXISTS source_articles (
    source_name      TEXT         NOT NULL,
    article_id       VARCHAR(32)  NOT NULL,
    url              TEXT         NOT NULL,
    title            TEXT,
    published_at     TIMESTAMPTZ,
    modified_at      TIMESTAMPTZ,
    content_hash     CHAR(64),
    status           VARCHAR(20)  NOT NULL,
    matched_food_ids TEXT[]       NOT NULL DEFAULT '{}',
    first_fetched_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_changed_at  TIMESTAMPTZ,
    PRIMARY KEY (source_name, article_id),
    CONSTRAINT ck_source_article_status CHECK (status IN ('matched', 'no_match', 'not_found'))
);
