"""Verify that inherited datasets and import commands still match origin/main."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "007e51dff66069195544468c0ba5f7020cafd303"
PREFIXES = ("data/", "nutrition_model/data/", "nutrition_target/rules/", "config/")
IMPORTERS = {
    "backend/app/services/recipe_import.py", "backend/app/services/nutrition_import.py",
    "scripts/import_food_workbooks.py", "scripts/import_recipes.py",
    "nutrition_model/import_tfda.py", "nutrition_model/import_workbook.py",
    "nutrition_model/import_usda_nutrition.py", "nutrition_model/import_usda_yields.py",
}


def verify() -> dict:
    entries = subprocess.check_output(["git", "ls-tree", "-rz", BASE], cwd=ROOT).decode().split("\0")
    rows = []
    for entry in entries:
        if not entry:
            continue
        _, path = entry.split("\t", 1)
        if not (path.startswith(PREFIXES) or path in IMPORTERS):
            continue
        original = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
        actual = (ROOT / path).read_bytes() if (ROOT / path).is_file() else b""
        # Git checkouts may have CRLF for text files; compare normalized text only.
        binary = b"\0" in original or path.endswith((".xlsx", ".gz", ".pt"))
        comparable = actual if binary else actual.replace(b"\r\n", b"\n")
        expected = original if binary else original.replace(b"\r\n", b"\n")
        rows.append({"path": path, "unchanged": comparable == expected,
                     "source_sha256": hashlib.sha256(original).hexdigest()})
    return {"base": BASE, "passed": all(r["unchanged"] for r in rows), "files": rows}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
