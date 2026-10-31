CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS knowledge_records (
    seq bigserial UNIQUE,
    knowledge_id text NOT NULL,
    source_version text NOT NULL,
    ingredient_id text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    reviewer text,
    review_note text,
    reviewed_at timestamptz,
    PRIMARY KEY (knowledge_id, source_version),
    CHECK (status = 'pending' OR (reviewer IS NOT NULL AND review_note IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS knowledge_ingredient_idx ON knowledge_records (ingredient_id);
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
