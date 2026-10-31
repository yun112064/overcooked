"""Pinned Ollama embeddinggemma adapter. No cloud fallback or model downloads."""
import threading

import httpx

from knowledge_rag.store import vector

LOCAL_MODEL = 'embeddinggemma:latest'
LOCAL_DIMENSIONS = 768
LOCAL_TEMPLATE = 't11-embeddinggemma-document-query-v1'
_SLOTS = threading.BoundedSemaphore(1)


class LocalEmbeddingUnavailable(Exception):
    pass


class OllamaEmbedder:
    dimensions = LOCAL_DIMENSIONS

    def __init__(self, url='http://127.0.0.1:11434', *, transport=None):
        self.url = url.rstrip('/')
        self.transport = transport
        self.digest = self._digest()
        self.model = LOCAL_MODEL + '@' + self.digest

    def _digest(self):
        with httpx.Client(transport=self.transport, timeout=10) as client:
            response = client.get(self.url + '/api/tags')
            response.raise_for_status()
            rows = response.json()['models']
        row = next((r for r in rows if r['name'] == LOCAL_MODEL), None)
        if row is None or not isinstance(row.get('digest'), str) or len(row['digest']) != 64:
            raise LocalEmbeddingUnavailable('LOCAL_MODEL_NOT_INSTALLED')
        return row['digest']

    def embed(self, texts):
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('Nonempty embedding texts required')
        if not _SLOTS.acquire(timeout=90):
            raise LocalEmbeddingUnavailable('LOCAL_EMBEDDING_BUSY')
        try:
            if self._digest() != self.digest:
                raise LocalEmbeddingUnavailable('LOCAL_MODEL_CHANGED')
            with httpx.Client(transport=self.transport, timeout=90) as client:
                response = client.post(self.url + '/api/embed', json=dict(
                    model=LOCAL_MODEL, input=texts, truncate=False, keep_alive='10m',
                    options={'num_ctx': 2048}))
                response.raise_for_status()
                body = response.json()
            if body.get('model') not in (LOCAL_MODEL, 'embeddinggemma') or self._digest() != self.digest:
                raise LocalEmbeddingUnavailable('LOCAL_MODEL_CHANGED')
            values = body['embeddings']
            if not isinstance(values, list) or len(values) != len(texts):
                raise ValueError('Embedding count mismatch')
            for value in values:
                vector(value, LOCAL_DIMENSIONS)
            return values
        finally:
            _SLOTS.release()

    def query(self, text):
        return self.embed(['task: search result | query: ' + text])[0]

    def documents(self, texts):
        return self.embed(['title: ingredient nutrition | text: ' + text for text in texts])
