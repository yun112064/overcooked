"""Run the standalone RAG service without the backend package or real models."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("failure", ["ollama", "http", "store"])
def test_standalone_knowledge_failure_returns_503_without_private_logs(tmp_path, failure):
    root = Path(__file__).resolve().parents[1]
    # The production image includes knowledge_rag, but has no backend package.
    shutil.copytree(root / "knowledge_rag", tmp_path / "knowledge_rag",
                    ignore=shutil.ignore_patterns("__pycache__"))
    code = r'''
import io
import logging
import sys
sys.path.insert(0, sys.argv[1])
import httpx
from fastapi.testclient import TestClient
from knowledge_rag.local_embedding import LocalEmbeddingUnavailable
from knowledge_rag.server import make_app
from knowledge_rag.store import KnowledgeStoreError
private = 'private-provider-details'
failure = sys.argv[2]
class Embedder:
    model = 'synthetic'
    def query(self, text):
        if failure == 'ollama':
            raise LocalEmbeddingUnavailable(private)
        if failure == 'http':
            raise httpx.ConnectError(private)
        return [0.0]
class Store:
    def search(self, *args, **kwargs):
        raise KnowledgeStoreError(private)
stream = io.StringIO()
logging.getLogger('knowledge_rag').addHandler(logging.StreamHandler(stream))
app = make_app(Store(), 'synthetic-private-token', Embedder())
response = TestClient(app).post('/v1/retrieve',
    headers={'Authorization': 'Bearer synthetic-private-token'},
    json={'query_text': 'private-user-question', 'food_ids': ['FOOD000678']})
assert response.status_code == 503, response.text
assert response.json() == {'detail': 'KNOWLEDGE_UNAVAILABLE'}
assert 'local_knowledge_unavailable' in stream.getvalue()
for value in (private, 'private-user-question', 'synthetic-private-token'):
    assert value not in stream.getvalue() + response.text
print('standalone offline path and private logging passed')
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path), failure],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
