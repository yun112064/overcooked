"""Formal worker's T11 boundary uses the same local index as preview; no paid fallback."""
import pytest

from app.services import knowledge
from app.services.team_link import TeamCredentials


def response():
    return {
        "records": [{"knowledge_id": "test", "source_version": "test-v1", "ingredient_id": "FOOD000001",
                     "excerpt": "synthetic test evidence", "source_url": "https://example.test/source",
                     "rights": "synthetic", "conditions": "test only", "status": "approved",
                     "reviewer": "test", "reviewed_at": "2026-11-11T00:00:00Z"}],
        "index_version": "isolated-test", "embedding_model": "embeddinggemma:latest@" + "a" * 64,
        "embedding_dimensions": 768, "template_version": "t11-embeddinggemma-document-query-v1",
        "status": "available", "by_ingredient": {"FOOD000001": "available"},
    }


def run(client):
    def forbidden(**kwargs):
        pytest.fail("Local retrieval must never call paid embedding")
    return knowledge.retrieve_knowledge(final_food_ids=["FOOD000001"], query_text="nutrition",
                                         client=client, embedder=object(), paid_call=forbidden)


def test_formal_generation_uses_local_retrieve(monkeypatch):
    client = knowledge.HttpKnowledgeClient("http://isolated.test", None, 120, provider="ollama")
    calls = []
    def post(payload, *, path):
        calls.append((payload, path))
        return response()
    monkeypatch.setattr(client, "_post", post)
    result = run(client)
    assert result["status"] == "available"
    assert result["index_version"] == "isolated-test"
    assert calls == [({"food_ids": ["FOOD000001"], "query_text": "nutrition"}, "/v1/retrieve")]


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(embedding_dimensions=1536),
    lambda r: r.update(embedding_model="embeddinggemma:latest"),
    lambda r: r.update(by_ingredient={"FOOD000001": "not_found"}),
    lambda r: r["records"][0].update(status="pending"),
    lambda r: r["records"][0].update(ingredient_id="FOOD000002"),
])
def test_local_invalid_response_never_falls_back_to_paid(monkeypatch, mutation):
    body = response()
    mutation(body)
    client = knowledge.HttpKnowledgeClient("http://isolated.test", None, 120, provider="ollama")
    monkeypatch.setattr(client, "_post", lambda *args, **kwargs: body)
    assert run(client)["status"] == "unavailable"


def test_local_failure_remains_unavailable(monkeypatch):
    client = knowledge.HttpKnowledgeClient("http://isolated.test", None, 120, provider="ollama")
    def timeout(*args, **kwargs):
        raise knowledge.KnowledgeUnavailable("TIMEOUT")
    monkeypatch.setattr(client, "_post", timeout)
    result = run(client)
    assert result["status"] == "unavailable" and result["reason"] == "TIMEOUT"
