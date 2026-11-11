"""Exercise preview and formal-generation T11 paths against the same real local index."""
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services import knowledge, preview_knowledge
from app.services.team_link import TeamCredentials


def main():
    token = os.environ["KNOWLEDGE_SERVICE_TOKEN"]
    formal = knowledge.HttpKnowledgeClient("http://t11:8091", TeamCredentials(token), 120, provider="ollama")
    preview = preview_knowledge.HttpKnowledgeClient("http://t11:8091", token)
    def no_paid(**kwargs):
        raise AssertionError("Paid call must not occur")
    arguments = dict(final_food_ids=["FOOD000168"], query_text="水蓮菜的營養", embedder=None, paid_call=no_paid)
    a = knowledge.retrieve_knowledge(client=formal, **arguments)
    b = preview_knowledge.retrieve_knowledge(client=preview, **arguments)
    assert a["status"] == b["status"] == "available", (a["status"], b["status"])
    assert a["index_version"] == b["index_version"] == "1111_update-web-paper-r3-embeddinggemma-v1"
    assert a["embedding_model"] == b["embedding_model"]
    for result in (a, b):
        assert result["records"] and all(r["source_url"].startswith("https://") for r in result["records"])
    print(json.dumps({"formal_status": a["status"], "preview_status": b["status"],
                      "index_version": a["index_version"], "embedding_model": a["embedding_model"],
                      "records": len(a["records"]), "paid_calls": 0,
                      "sources": [r["source_url"] for r in a["records"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
