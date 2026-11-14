"""Apply the user's accepted T11 source review to the matching knowledge version."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from knowledge_rag.store import KnowledgeStore, same_imported_content, validate_record


ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates/web_paper_2026-10-30.json"
REVIEWS = ROOT / "reviews/web_paper_2026-10-30.json"
# Decision #29 classification of the same 123 records (prod run classify-prod-20261114), so every
# machine gets the nutrition facts explanations need without calling the classifier again.
CLASSIFICATIONS = ROOT / "reviews/web_paper_2026-10-30.classification.json"
EXPECTED_HASHES = {
    CANDIDATES: "fa391764bdc25762bb58fa2ac4a6a5902036078fa57f14b9ac3ed5ca5aeadd7c",
    REVIEWS: "f833ddf4d33b93254bc00797aac44b06846079082c3bc6ad957a6b1ca97f9cab",
    CLASSIFICATIONS: "4b0012d55b4de6412863225bd0e2cfe9fcd905853e82b8cca5718f3efd1c104a",
}


def _check_hash(path):
    digest = sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if digest != EXPECTED_HASHES[path]:
        raise ValueError(f"Reviewed file changed: {path.name}")


def checked_records():
    for path in (CANDIDATES, REVIEWS):
        _check_hash(path)
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


def checked_classifications(candidates):
    """Recorded classifier outcomes by knowledge_id, plus (model, prompt_version).

    Each nutrition outcome must still pass the program check a fresh classify run applies
    (knowledge_rag.classify.validate) against the candidate excerpt.
    """
    from knowledge_rag.classify import NUTRITION, validate
    from knowledge_rag.healthnews import load_nutrition_keywords
    _check_hash(CLASSIFICATIONS)
    data = json.loads(CLASSIFICATIONS.read_text(encoding="utf-8"))
    by_id = {row["knowledge_id"]: row for row in candidates}
    outcomes = {row["knowledge_id"]: row for row in data["records"]}
    if (len(outcomes) != len(data["records"]) or set(outcomes) != set(by_id)
            or {row["source_version"] for row in candidates} != {data["source_version"]}):
        raise ValueError("Classified knowledge IDs do not match candidates")
    keywords = load_nutrition_keywords()
    for kid, outcome in outcomes.items():
        if outcome["classification"] == NUTRITION:
            item = {"is_nutrition_knowledge": True, "nutrition_fact": outcome["nutrition_fact"],
                    "evidence_quote": outcome["evidence_quote"], "reason": outcome["classification_reason"]}
            checked = validate(by_id[kid], item, keywords)
            if any(checked[k] != outcome[k] for k in ("classification", "nutrition_fact", "evidence_quote")):
                raise ValueError(f"Recorded classification fails validation: {kid}")
        elif (outcome["classification"] not in ("not_nutrition", "invalid")
              or outcome["nutrition_fact"] is not None or outcome["evidence_quote"] is not None):
            raise ValueError(f"Unexpected recorded classification: {kid}")
    return outcomes, data["classifier_model"], data["classifier_prompt_version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Import and approve in configured knowledge DB")
    args = parser.parse_args()
    candidates, decisions = checked_records()
    if not args.apply:
        print(json.dumps({"matched": len(decisions), "database_written": False}))
        return
    store = KnowledgeStore(os.environ["KNOWLEDGE_DATABASE_URL"], embedding_model="embeddinggemma:latest")
    print(json.dumps({**apply(store, candidates, decisions), "index_built": False}))


def apply(store, candidates, decisions):
    """Import, approve and classify the checked records; existing approvals and classifications are kept."""
    outcomes, model, prompt_version = checked_classifications(candidates)
    store.initialize()
    store.import_records(candidates)
    approved = 0
    by_id = {row["knowledge_id"]: row for row in candidates}
    with store.connect() as conn:
        stored = {(r["knowledge_id"], r["source_version"]): r for r in conn.execute(
            "SELECT knowledge_id,source_version,status,payload,classification FROM knowledge_records "
            "WHERE source_version=%s FOR UPDATE", (candidates[0]["source_version"],)).fetchall()}
        if len(stored) != 123 or any(
            (entry := stored.get((row["knowledge_id"], row["source_version"]))) is None
            or entry["status"] not in ("pending", "approved")
            or not same_imported_content(entry["payload"], validate_record(row)) for row in candidates
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
    unclassified = [row for row in candidates
                    if stored[(row["knowledge_id"], row["source_version"])]["classification"] is None]
    for row in unclassified:
        outcome = outcomes[row["knowledge_id"]]
        store.save_classification(row["knowledge_id"], row["source_version"],
                                  {"classification": outcome["classification"],
                                   "reason": outcome["classification_reason"],
                                   "nutrition_fact": outcome["nutrition_fact"],
                                   "evidence_quote": outcome["evidence_quote"]},
                                  model=model, prompt_version=prompt_version)
    return {"approved_now": approved, "already_approved": 123 - approved,
            "classified_now": len(unclassified), "already_classified": 123 - len(unclassified)}


if __name__ == "__main__":
    main()
