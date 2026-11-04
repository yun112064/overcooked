"""T11 FastAPI HTTP contract checks; no PostgreSQL or external embedding service."""
from fastapi.testclient import TestClient


class Store:
    def search(self, food_ids, query_embedding, embedding_model, *, index_version=None):
        return {"status": "not_found", "records": [],
                "by_ingredient": {food_id: "not_found" for food_id in food_ids},
                "index_version": "idx-test", "embedding_model": embedding_model,
                "template_version": "t11-test"}


def test_current_client_route_and_authentication():
    from knowledge_rag.server import make_app
    client = TestClient(make_app(Store(), "secret"))
    payload = {"food_ids": ["FOOD000296"], "query_embedding": [0.1] * 1536,
               "embedding_model": "text-embedding-3-small", "limit_per_ingredient": 3}

    denied = client.post("/v1/knowledge/search", json=payload)
    assert denied.status_code == 401
    response = client.post("/v1/knowledge/search", json=payload,
                           headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["by_ingredient"] == {"FOOD000296": "not_found"}


def test_search_store_failure_is_service_unavailable_not_empty_result():
    from knowledge_rag.server import make_app

    class BrokenStore:
        def search(self, *args, **kwargs):
            from knowledge_rag.store import KnowledgeStoreError
            raise KnowledgeStoreError("STORE_UNAVAILABLE")

    client = TestClient(make_app(BrokenStore(), "secret"))
    response = client.post("/v1/knowledge/search", json={
        "food_ids": ["FOOD000296"], "query_embedding": [0.1] * 1536,
        "embedding_model": "text-embedding-3-small"}, headers={"Authorization": "Bearer secret"})
    assert response.status_code == 503
    assert response.json()["detail"] == "STORE_UNAVAILABLE"
