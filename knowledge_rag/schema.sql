CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS knowledge_records (
    seq bigserial UNIQUE,
    knowledge_id text NOT NULL,
    source_version text NOT NULL,
    ingredient_id text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now(),
    excerpt text NOT NULL DEFAULT '',
    source_url text NOT NULL DEFAULT '',
    rights text NOT NULL DEFAULT '',
    conditions text NOT NULL DEFAULT '',
    nutrition_fact text,
    evidence_quote text,
    fetched_at timestamptz,
    content_provider text,
    authorization_ref text,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    reviewer text,
    review_note text,
    reviewed_at timestamptz,
    PRIMARY KEY (knowledge_id, source_version),
    CHECK (status = 'pending' OR (reviewer IS NOT NULL AND review_note IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS knowledge_ingredient_idx ON knowledge_records (ingredient_id);
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS imported_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS excerpt text NOT NULL DEFAULT '';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS source_url text NOT NULL DEFAULT '';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS rights text NOT NULL DEFAULT '';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS conditions text NOT NULL DEFAULT '';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS nutrition_fact text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS evidence_quote text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS fetched_at timestamptz;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS content_provider text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS authorization_ref text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS classification text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS classification_reason text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS classified_at timestamptz;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS classifier_model text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS classifier_prompt_version text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS article_id text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS content_hash text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS source_name text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS license_status text;
CREATE TABLE IF NOT EXISTS source_articles (
    source_name text NOT NULL,
    article_id text NOT NULL,
    url text NOT NULL,
    title text,
    published_at timestamptz,
    modified_at timestamptz,
    content_hash text,
    status text NOT NULL,
    matched_food_ids text[] NOT NULL DEFAULT '{}',
    first_fetched_at timestamptz NOT NULL DEFAULT now(),
    last_fetched_at timestamptz NOT NULL DEFAULT now(),
    last_changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_name, article_id)
);
CREATE TABLE IF NOT EXISTS knowledge_indexes (
    index_version text PRIMARY KEY,
    embedding_model text NOT NULL,
    template_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('building','active','retired')),
    record_count integer NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_one_active ON knowledge_indexes(status) WHERE status='active';
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    index_version text REFERENCES knowledge_indexes(index_version),
    knowledge_id text NOT NULL,
    source_version text NOT NULL,
    embedding vector(1536) NOT NULL,
    PRIMARY KEY(index_version,knowledge_id,source_version),
    FOREIGN KEY(knowledge_id,source_version) REFERENCES knowledge_records(knowledge_id,source_version)
);
