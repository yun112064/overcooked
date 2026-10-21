from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.recipe_import import (  # noqa: E402
    CATEGORY_OVERRIDES_PATH,
    import_recipe_bundle,
    load_category_overrides,
    load_review_record,
    load_source_rights,
    parse_recipe_workbook,
    write_quality_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import the six-table T01 recipe workbook")
    parser.add_argument("--excel", required=True, help="Path to the source .xlsx workbook")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL; defaults to DATABASE_URL",
    )
    parser.add_argument("--report", help="Write the data-quality report to this JSON path")
    parser.add_argument(
        "--reviews",
        help="JSON human-review record bound to the workbook SHA-256 (e.g. config/recipe_reviews.json)",
    )
    parser.add_argument(
        "--sources",
        help="JSON with per-source license/rights/version defaults (e.g. config/recipe_sources.json)",
    )
    parser.add_argument(
        "--category-overrides",
        default=str(CATEGORY_OVERRIDES_PATH),
        help="Recipe category corrections (decision #38); pass an empty string to skip",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing to the database",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_rights = load_source_rights(args.sources) if args.sources else None
    review_record = load_review_record(args.reviews) if args.reviews else None
    overrides = load_category_overrides(args.category_overrides) if args.category_overrides else None
    bundle = parse_recipe_workbook(args.excel, source_rights, review_record, overrides)
    if review_record and not (bundle.report.get("review_record") or {}).get("applied"):
        print("WARNING: review record not applied: " + str((bundle.report.get("review_record") or {}).get("reason")),
              file=sys.stderr)
    if args.report:
        write_quality_report(bundle.report, args.report)

    result: dict[str, object] = {"quality": bundle.report["counts"],
                                 "category_overrides_applied": len(bundle.report["category_overrides"]["applied"])}
    if args.dry_run:
        result["database"] = "not_written"
    else:
        if not args.database_url:
            raise SystemExit("--database-url or DATABASE_URL is required unless --dry-run is used")
        engine = create_engine(args.database_url, pool_pre_ping=True)
        with Session(engine) as session, session.begin():
            result["database"] = import_recipe_bundle(session, bundle)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

