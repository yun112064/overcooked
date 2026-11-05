"""Read Git trees without importing application code or connecting to databases."""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = "refs/remotes/origin/t11-t20-demo"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def tree(ref: str) -> dict[str, dict]:
    result = {}
    for entry in subprocess.check_output(
        ["git", "ls-tree", "-rz", ref], cwd=ROOT
    ).decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, oid = meta.split()
        result[path] = {"mode": mode, "kind": kind, "blob": oid}
    return result


def snapshot() -> dict:
    refs = [r for r in git("for-each-ref", "--format=%(refname)",
                          "refs/remotes/origin").splitlines()
            if r not in (EXCLUDED, "refs/remotes/origin/HEAD")]
    base = tree("origin/main")
    branches = []
    for ref in refs:
        files = tree(ref)
        changed = sorted(p for p in files.keys() | base.keys()
                         if files.get(p) != base.get(p))
        sources = [p for p in files if (
            p.startswith(("data/", "nutrition_model/data/", "knowledge_rag/sources/",
                          "knowledge_rag/candidates/", "knowledge_rag/reviews/"))
            or p.endswith((".xlsx", ".csv", ".sql", ".sql.gz", ".pt"))
            or p.startswith("config/") or "manifest" in p.lower())]
        branches.append({
            "branch": ref.removeprefix("refs/remotes/origin/"),
            "sha": git("rev-parse", ref), "files": files,
            "changed_from_main": changed,
            "changed_roots": dict(Counter(p.split("/")[0] for p in changed)),
            "source_candidates": sorted(sources),
        })
    return {"excluded_branch": "t11-t20-demo", "base": git("rev-parse", "origin/main"),
            "branches": branches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Audited {len(output['branches'])} branches; excluded {output['excluded_branch']}")


if __name__ == "__main__":
    main()
