"""Publish the 123 reviewed real records only into the explicitly isolated acceptance DB.

This is a local acceptance release, not a transfer of formal publication approval or
third-party rights. Source files and their original approval fields stay untouched.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from knowledge_rag.local_embedding import OllamaEmbedder
from knowledge_rag.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[3]
DB_NAME = "meal_planner_1102_v3_1111_update_v2_knowledge"
INDEX = "1111_update-web-paper-r3-embeddinggemma-v1"


def main():
    dsn = os.environ["KNOWLEDGE_DATABASE_URL"]
    if urlsplit(dsn).hostname != "t11-db" or urlsplit(dsn).path != "/" + DB_NAME:
        raise SystemExit("Refusing to publish outside the 1111_update isolated knowledge database")
    source_path = ROOT / "knowledge_rag/candidates/web_paper_2026-10-30.json"
    review_path = ROOT / "knowledge_rag/reviews/web_paper_2026-10-30.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    decisions = {r["knowledge_id"]: r for r in reviews["decisions"]}
    records = source["records"]
    assert len(records) == len(decisions) == 123
    for row in records:
        review = decisions[row["knowledge_id"]]
        assert review["source_content_decision"] == "accepted_under_user_policy"
        assert review["ingredient_id"] == row["ingredient_id"]
        assert review["source_url"] == row["source_url"]
        assert review["fact_zh_reviewed"] == row["fact_zh"]
        assert review["conditions_reviewed"] == row["conditions"]
    embedder = OllamaEmbedder(os.environ["OLLAMA_URL"])
    store = KnowledgeStore(dsn, embedding_model=embedder.model)
    store.initialize()
    report = store.import_records(records)
    for row in records:
        current = store.get_record(row["knowledge_id"])
        if current["status"] == "pending":
            store.review(row["knowledge_id"], row["source_version"], approved=True,
                         reviewer="1111_update-isolated-acceptance",
                         note="2026-11-11 user requested isolated acceptance index of these 123 reviewed records; "
                              "source/review JSON matched exactly. Not formal publication or rights certification.")
        elif current["status"] != "approved":
            raise RuntimeError("Unexpected review state; refusing automatic override")
    active = store.active_index()
    if active and active["index_version"] == INDEX:
        if active["embedding_model"] != embedder.model:
            raise RuntimeError("Pinned model changed; use a new acceptance index version")
        report["index"] = active
    else:
        def embed_payloads(payloads):
            # The record stores full source provenance. Embed only its reviewed text.
            rows = [json.loads(p) for p in payloads]
            texts = [f"{r.get('ingredient_name', r['ingredient_id'])}：{r.get('fact_zh', r['excerpt'])}\n{r['conditions']}"
                     for r in rows]
            return embedder.documents(texts)
        report["index"] = store.build_index(INDEX, embedder.model, embed_payloads, batch_size=4)
    report.update(source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                  review_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),
                  acceptance_only=True, formal_publication_approved=False, synthetic_records=0)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
