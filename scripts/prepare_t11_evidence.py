"""Prepare real T11 evidence without inferring publication approval.

Default: offline dry-run. --fetch downloads only three existing approved source URLs,
honours robots and >=2s spacing, and writes text snapshots outside the repository.
--apply imports a hash-pinned bundle as PENDING into one marker-verified isolated DB.
Review and index publication remain separate, explicit operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "knowledge_rag/candidates/web_paper_2026-10-30.json"
REVIEW = ROOT / "knowledge_rag/reviews/web_paper_2026-10-30.json"
SELECTED = ("WEB-048-001--HN-54202", "WEB-021-001--HN-66393", "WEB-004-001--HY-water")
DB = "meal_planner_1102_v3_1111_update_v2_knowledge"
MARKER = "overcooked:1111_update_v2:t11-evidence"
POLICY = "t11-literal-nutrition-candidate-v1"
USER_AGENT = "overcooked-evidence/1.0 (non-commercial student project; +https://github.com/yun112064/overcooked)"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def immutable_write(path: Path, value) -> None:
    data = encoded(value)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Refusing to overwrite an existing evidence snapshot")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def data_root(value: str | None) -> Path:
    if not value:
        raise ValueError("--data-root or T11_EVIDENCE_DATA_ROOT is required")
    path = Path(value).resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("Raw source text must be outside the repository, in its Data Root")
    return path


def source_rows():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    decisions = {r["knowledge_id"]: r for r in json.loads(REVIEW.read_text(encoding="utf-8"))["decisions"]}
    rows = {r["knowledge_id"]: r for r in source["records"]}
    selected = []
    for key in SELECTED:
        row, review = rows[key], decisions[key]
        if (review["source_content_decision"] != "accepted_under_user_policy"
                or review["source_url"] != row["source_url"]
                or review["ingredient_id"] != row["ingredient_id"]):
            raise ValueError("Original source review does not match selected record")
        selected.append(row)
    return selected


def literal_candidate(article, row, selection=None):
    """Select a short original clause, never re-label an editorial summary as a quote.

    This mechanical selector proposes candidates only. It cannot approve factual,
    medical or usage-rights claims. A human reviewer must inspect the full paragraph.
    """
    from knowledge_rag.classify import validate
    if selection is not None:
        matches = []
        for section_no, section in enumerate(article["sections"]):
            for paragraph_no, paragraph in enumerate(section["paragraphs"]):
                if (selection["evidence_quote"] in paragraph
                        and selection["paragraph_contains"] in paragraph):
                    outcome = validate({"excerpt": paragraph}, {**selection, "is_nutrition_knowledge": True})
                    if outcome["classification"] != "nutrition":
                        raise ValueError("Selected sentence fails existing evidence validator")
                    matches.append({"excerpt": paragraph, "nutrition_fact": outcome["nutrition_fact"],
                                    "evidence_quote": outcome["evidence_quote"], "section_no": section_no,
                                    "paragraph_no": paragraph_no, "classification": outcome["classification"],
                                    "classification_reason": outcome["reason"]})
        if len(matches) != 1:
            raise ValueError("Selection must match exactly one current source paragraph")
        return matches[0]
    # Without explicit selections, retain raw snapshots but invent no candidate.
    return None


def fetch(root: Path, selections=None):
    from assistant_rag.sources import healthnews, helloyishi
    from knowledge_rag.fetcher import PoliteFetcher
    modules = {m.HOST: m for m in (healthnews, helloyishi)}
    fetchers = {host: PoliteFetcher(allowed_host=host, user_agent=USER_AGENT) for host in modules}
    records, unresolved, snapshots = [], [], []
    for original in source_rows():
        url = original["source_url"]
        host = urlsplit(url).hostname
        raw = fetchers[host].get(url)
        article = modules[host].parse(raw, url) if raw else None
        if not article or article.get("skip"):
            unresolved.append({"knowledge_id": original["knowledge_id"], "reason": "SOURCE_PARSE_OR_FETCH_FAILED"})
            continue
        # Only parsed raw text is retained; no HTML, images or third-party code.
        snapshot_hash = digest(encoded(article))
        relative = f"snapshots/{snapshot_hash}.json"
        immutable_write(root / relative, article)
        snapshots.append({"path": relative, "sha256": snapshot_hash, "source_url": url})
        candidate = literal_candidate(article, original, (selections or {}).get(original["knowledge_id"]))
        if candidate is None:
            unresolved.append({"knowledge_id": original["knowledge_id"], "reason": "NO_SAFE_SHORT_LITERAL_CANDIDATE",
                               "snapshot": relative})
            continue
        version_hash = digest(encoded({"snapshot_sha256": snapshot_hash, "candidate": candidate, "policy": POLICY}))
        records.append({**candidate,
            "knowledge_id": original["knowledge_id"] + "--evidence-v2",
            "source_version": "raw-evidence-" + version_hash[:24],
            "parent_knowledge_id": original["knowledge_id"], "parent_source_version": original["source_version"],
            "ingredient_id": original["ingredient_id"], "ingredient_name": original["ingredient_name"],
            "source_url": url, "source_name": article["source_name"], "title": article["title"],
            "published_at": article.get("published_at"), "fetched_at": article["fetched_at"],
            "content_provider": article.get("content_provider"),
            "license_status": "third_party_unconfirmed" if article.get("content_provider") else None,
            "rights": original["rights"], "conditions": original["conditions"],
            "excerpt_kind": "current_original_paragraph", "content_hash": article["content_hash"],
            "raw_snapshot_path": relative, "raw_snapshot_sha256": snapshot_hash,
            "classification_method": POLICY, "status": "pending",
            "review_required": True, "formal_index_approval_granted": False,
        })
    bundle = {"schema": "t11-evidence-candidates/v1", "policy": POLICY,
              "original_source_sha256": digest(SOURCE.read_bytes()), "original_review_sha256": digest(REVIEW.read_bytes()),
              "records": records, "snapshots": snapshots, "unresolved": unresolved,
              "publication_approved": False, "paid_model_calls": 0}
    if selections:
        bundle["candidate_selection_sha256"] = digest(encoded(selections))
    bundle_hash = digest(encoded(bundle))
    path = root / f"candidates-{bundle_hash}.json"
    immutable_write(path, bundle)
    return {"bundle": str(path), "bundle_sha256": bundle_hash, "candidates": len(records),
            "source_snapshots": len(snapshots), "unresolved": unresolved, "status": "pending_review",
            "database_written": False, "paid_model_calls": 0}


def validate_bundle(root, bundle_path, expected_hash):
    from knowledge_rag.classify import validate
    path = Path(bundle_path).resolve()
    if root not in path.parents or not re.fullmatch(r"[0-9a-f]{64}", expected_hash or ""):
        raise ValueError("Bundle must be inside Data Root and pinned by SHA-256")
    if digest(path.read_bytes()) != expected_hash:
        raise ValueError("Bundle hash mismatch")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if (bundle.get("schema") != "t11-evidence-candidates/v1" or bundle.get("policy") != POLICY
            or bundle.get("publication_approved") is not False
            or bundle["original_source_sha256"] != digest(SOURCE.read_bytes())
            or bundle["original_review_sha256"] != digest(REVIEW.read_bytes())):
        raise ValueError("Source or review version changed")
    originals = {r["knowledge_id"]: r for r in source_rows()}
    if not 1 <= len(bundle["records"]) <= 3:
        raise ValueError("Only the three bounded acceptance source candidates are allowed")
    for record in bundle["records"]:
        original = originals[record["parent_knowledge_id"]]
        raw_path = (root / record["raw_snapshot_path"]).resolve()
        if root not in raw_path.parents or digest(raw_path.read_bytes()) != record["raw_snapshot_sha256"]:
            raise ValueError("Raw snapshot hash/path mismatch")
        article = json.loads(raw_path.read_text(encoding="utf-8"))
        if record["excerpt"] != article["sections"][record["section_no"]]["paragraphs"][record["paragraph_no"]]:
            raise ValueError("Excerpt is not the stored original paragraph")
        selected = {key: record[key] for key in ("excerpt", "nutrition_fact", "evidence_quote", "section_no",
                    "paragraph_no", "classification", "classification_reason")}
        version_hash = digest(encoded({"snapshot_sha256": record["raw_snapshot_sha256"],
                                      "candidate": selected, "policy": POLICY}))
        if record["source_version"] != "raw-evidence-" + version_hash[:24]:
            raise ValueError("Candidate source version does not match its content hash")
        if (record["source_url"] != original["source_url"] or record["ingredient_id"] != original["ingredient_id"]
                or record["rights"] != original["rights"] or record["conditions"] != original["conditions"]
                or record["content_provider"] != article.get("content_provider")
                or record["license_status"] != ("third_party_unconfirmed" if article.get("content_provider") else None)
                or record["status"] != "pending" or record["formal_index_approval_granted"] is not False
                or record["evidence_quote"] not in record["excerpt"]
                or record["knowledge_id"] != original["knowledge_id"] + "--evidence-v2"):
            raise ValueError("Candidate provenance or pending state mismatch")
        outcome = validate(record, {**record, "is_nutrition_knowledge": True})
        if outcome["classification"] != "nutrition":
            raise ValueError("Candidate failed existing nutrition evidence validation")
    return bundle


def apply(root, args):
    from knowledge_rag.store import KnowledgeStore
    bundle = validate_bundle(root, args.bundle, args.bundle_sha256)
    dsn = os.environ.get(args.database_url_env, "")
    url = urlsplit(dsn)
    if url.scheme != "postgresql" or url.hostname != "t11-db" or url.path != "/" + DB:
        raise ValueError("Refusing database outside exact 1111_update_v2 isolated T11 target")
    store = KnowledgeStore(dsn, embedding_model="embeddinggemma:latest")
    with store.connect() as conn:
        marker = conn.execute("SELECT shobj_description(oid, 'pg_database') AS marker FROM pg_database "
                              "WHERE datname=current_database()").fetchone()
        if not marker or marker["marker"] != MARKER:
            raise ValueError("Isolated database marker missing; no data written")
    return {**store.import_records(bundle["records"]), "database": DB,
            "review_applied": False, "index_published": False, "bundle_sha256": args.bundle_sha256}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--data-root", default=os.getenv("T11_EVIDENCE_DATA_ROOT"))
    parser.add_argument("--bundle")
    parser.add_argument("--bundle-sha256")
    parser.add_argument("--database-url-env", default="KNOWLEDGE_DATABASE_URL")
    parser.add_argument("--selections", help="Optional Data Root JSON of explicit candidate facts and literal quotes; never approval")
    args = parser.parse_args(argv)
    if args.fetch:
        root = data_root(args.data_root)
        selections = None
        if args.selections:
            selection_path = Path(args.selections).resolve()
            if root not in selection_path.parents:
                raise ValueError("Selections must stay in Data Root")
            selections = json.loads(selection_path.read_text(encoding="utf-8"))
            if set(selections) != set(SELECTED):
                raise ValueError("Selections must cover exactly the three original sources")
        report = fetch(root, selections)
    elif args.apply:
        if not args.bundle or not args.bundle_sha256:
            parser.error("--apply requires --bundle and --bundle-sha256")
        report = apply(data_root(args.data_root), args)
    else:
        report = {"dry_run": True, "network_calls": 0, "database_written": False,
                  "target_database": DB, "required_database_marker": MARKER,
                  "sources": [{k: r[k] for k in ("knowledge_id", "ingredient_id", "source_url")} for r in source_rows()]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
