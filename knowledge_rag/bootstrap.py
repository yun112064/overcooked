"""Local dev setup: apply the reviewed 123 records with their recorded classification,
and publish an index if none matches.

Run by scripts/start-dev.ps1 against the dev knowledge database.  Uses only the
locally installed Ollama embeddinggemma; never downloads a model or calls a paid
API.  Without the model the records are still approved and the index is skipped.
"""
from datetime import datetime, timezone
import json
import os

import httpx

from knowledge_rag.apply_reviewed import apply, checked_records
from knowledge_rag.local_embedding import LOCAL_MODEL, LocalEmbeddingUnavailable, OllamaEmbedder
from knowledge_rag.store import KnowledgeStore


def active_index_matches(store, model):
    """True when the active index uses this model and covers every approved record."""
    with store.connect() as conn:
        index = conn.execute("SELECT index_version,embedding_model FROM knowledge_indexes "
                             "WHERE status='active'").fetchone()
        if index is None or index['embedding_model'] != model:
            return False
        missing = conn.execute(
            "SELECT count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
            "ORDER BY knowledge_id,seq DESC) c WHERE c.status='approved' AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_embeddings e WHERE e.index_version=%s "
            "AND e.knowledge_id=c.knowledge_id AND e.source_version=c.source_version)",
            (index['index_version'],)).fetchone()
        return missing is not None and missing['n'] == 0


def main():
    candidates, decisions = checked_records()
    url = os.environ['KNOWLEDGE_DATABASE_URL']
    result = apply(KnowledgeStore(url, embedding_model=LOCAL_MODEL), candidates, decisions)
    try:
        local = OllamaEmbedder(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434'))
    except (LocalEmbeddingUnavailable, httpx.HTTPError, ValueError) as exc:
        print(json.dumps({**result, 'index_built': False, 'index_skipped': f'ollama: {exc or type(exc).__name__}'}))
        return
    store = KnowledgeStore(url, embedding_model=local.model)
    if active_index_matches(store, local.model):
        print(json.dumps({**result, 'index_built': False, 'index_skipped': 'active index is current'}))
        return
    version = 'dev-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    print(json.dumps({**result, 'index_built': True, **store.build_index(version, local.model, local.embed)}))


if __name__ == '__main__':
    main()
