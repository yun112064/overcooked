"""Run the read-only T12 Food_ID import audit against a configured database."""
from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine

from nutrition_planning.audit import audit_food_id_coverage


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit T12 recipe Food_ID coverage.')
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--import-id', required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    with engine.connect() as db:
        report = audit_food_id_coverage(db, import_id=args.import_id)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report['ready_for_food_id_resolution'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
