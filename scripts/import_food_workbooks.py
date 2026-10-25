"""Validate/import recipes, their food dictionary, and versioned nutrition together."""
import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.nutrition_import import import_nutrition_bundle, parse_nutrition_workbooks
from app.services.recipe_import import load_review_record, load_source_rights, write_quality_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes", required=True, help="Six-table recipe workbook")
    parser.add_argument("--catalog", required=True, help="Food ID dictionary workbook")
    parser.add_argument("--nutrition", required=True, help="Workbook with All and 原始來源與判定 sheets")
    parser.add_argument("--database-url", help="Defaults to DATABASE_URL in the environment or project .env")
    parser.add_argument("--sources", help="Existing source-rights JSON; does not fabricate rights")
    parser.add_argument("--reviews", help="Existing review JSON; only applies to its exact workbook hash")
    parser.add_argument("--report", required=True, help="JSON report path")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; no database connection")
    args = parser.parse_args()
    bundle = parse_nutrition_workbooks(
        args.recipes, args.catalog, args.nutrition,
        source_rights=load_source_rights(args.sources) if args.sources else None,
        review_record=load_review_record(args.reviews) if args.reviews else None,
    )
    report = {**bundle.report, "database": "not_written"}
    # Persist validation evidence even if a subsequent transaction fails.
    write_quality_report(report, args.report)
    if not args.dry_run:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        url = args.database_url or os.getenv("DATABASE_URL")
        if not url:
            parser.error("DATABASE_URL or --database-url is required for importing")
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with Session(engine) as session, session.begin():
                result = import_nutrition_bundle(session, bundle)
            report["database"] = result
            write_quality_report(report, args.report)
        finally:
            engine.dispose()
    print(json.dumps({"import_id": bundle.import_id, "counts": bundle.report["counts"],
                      "recipe_quality": bundle.recipe_bundle.report["counts"],
                      "import_selection": bundle.recipe_bundle.report["import_selection"],
                      "review_record": bundle.recipe_bundle.report["review_record"],
                      "database": report["database"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
