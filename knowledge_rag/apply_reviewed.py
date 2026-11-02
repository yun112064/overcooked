"""Apply the user's accepted T11 source review to the matching knowledge version."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from knowledge_rag.store import KnowledgeStore, validate_record


ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates/web_paper_2026-10-30.json"
REVIEWS = ROOT / "reviews/web_paper_2026-10-30.json"
EXPECTED_HASHES = {
    CANDIDATES: "fa391764bdc25762bb58fa2ac4a6a5902036078fa57f14b9ac3ed5ca5aeadd7c",
    REVIEWS: "f833ddf4d33b93254bc00797aac44b06846079082c3bc6ad957a6b1ca97f9cab",
}


def checked_records():
    for path in (CANDIDATES, REVIEWS):
        digest = sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if digest != EXPECTED_HASHES[path]:
            raise ValueError(f"Reviewed file changed: {path.name}")
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["records"]
    decisions = json.loads(REVIEWS.read_text(encoding="utf-8"))["decisions"]
    if len(candidates) != 123 or len(decisions) != 123:
        raise ValueError("Expected 123 candidate and reviewed records")
    by_id = {row["knowledge_id"]: row for row in candidates}
    if len(by_id) != 123 or {row["knowledge_id"] for row in decisions} != set(by_id):
        raise ValueError("Reviewed knowledge IDs do not match candidates")
    for decision in decisions:
        row = by_id[decision["knowledge_id"]]
        if (decision["source_content_decision"] != "accepted_under_user_policy"
                or row["excerpt"] != "整理摘要（非逐字原文）：" + decision["fact_zh_reviewed"]
                or any(decision[reviewed] != row[field] for reviewed, field in (
                    ("ingredient_id", "ingredient_id"), ("source_url", "source_url"),
                    ("fact_zh_reviewed", "fact_zh"), ("conditions_reviewed", "conditions")))):
            raise ValueError(f"Review differs from candidate: {decision['knowledge_id']}")
    return candidates, decisions


def review_note(decision):
    """Approval note that keeps how each source was checked.

    113 records carry the fetched page snapshot hash.  10 paper records were
    blocked by direct HTTP (403) and were checked against the publisher or
    PubMed via another tool; they have no snapshot hash, so the fetch status,
    access note and review method are recorded instead of failing the batch.
    """
    note = {"decision_basis": decision["decision_basis"],
            "review_version": "web-paper-source-review-2026-10-30-v2",
            "review_method": decision["review_method"],
            "user_acceptance": "2026-10-31"}
    if decision.get("source_snapshot_sha256"):
        note["source_snapshot_sha256"] = decision["source_snapshot_sha256"]
    elif decision.get("http_fetch_status") and decision.get("access_note"):
        note["http_fetch_status"] = decision["http_fetch_status"]
        note["access_note"] = decision["access_note"]
    else:
        raise ValueError(f"No source evidence recorded: {decision['knowledge_id']}")
    return json.dumps(note, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Import and approve in configured knowledge DB")
    args = parser.parse_args()
    candidates, decisions = checked_records()
    if not args.apply:
        print(json.dumps({"matched": len(decisions), "database_written": False}))
        return
    store = KnowledgeStore(os.environ["KNOWLEDGE_DATABASE_URL"], embedding_model="embeddinggemma:latest")
    store.initialize()
    store.import_records(candidates)
    approved = 0
    by_id = {row["knowledge_id"]: row for row in candidates}
    with store.connect() as conn:
        stored = {(r["knowledge_id"], r["source_version"]): r for r in conn.execute(
            "SELECT knowledge_id,source_version,status,payload FROM knowledge_records "
            "WHERE source_version=%s FOR UPDATE", (candidates[0]["source_version"],)).fetchall()}
        if len(stored) != 123 or any(
            (entry := stored.get((row["knowledge_id"], row["source_version"]))) is None
            or entry["status"] not in ("pending", "approved")
            or entry["payload"] != validate_record(row) for row in candidates
        ):
            raise ValueError("Stored knowledge differs from the reviewed snapshot")
        for decision in decisions:
            row = by_id[decision["knowledge_id"]]
            if stored[(row["knowledge_id"], row["source_version"])]["status"] == "approved":
                continue
            note = review_note(decision)
            conn.execute("UPDATE knowledge_records SET status='approved',reviewer=%s,review_note=%s,"
                         "reviewed_at=now() WHERE knowledge_id=%s AND source_version=%s",
                         (decision["reviewed_by"], note, row["knowledge_id"], row["source_version"]))
            approved += 1
    print(json.dumps({"approved_now": approved, "already_approved": 123 - approved,
                      "index_built": False}))


if __name__ == "__main__":
    main()
