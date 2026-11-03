"""Install only T04 observations/candidate bindings into the dedicated V3 DB."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.services.nutrition_release import T04_TABLES, load_bundle, install
from scripts.t04_db_support import guard, snapshot, HEAD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Commit; without this flag always roll back')
    args = parser.parse_args()
    from app.core.config import settings
    engine = create_engine(settings.database_url)
    guard(engine)
    bundle = load_bundle(args.manifest, args.sha256)
    with Session(engine) as session, session.begin() as transaction:
        if session.scalar(text('SELECT version_num FROM alembic_version')) != HEAD:
            raise ValueError('T04 schema migration must be applied first')
        before = snapshot(session.connection(), exclude=T04_TABLES)
        result = install(session, bundle)
        after = snapshot(session.connection(), exclude=T04_TABLES)
        if before != after:
            raise ValueError('Non-T04 table contents changed; rolling back')
        result['non_t04_tables_unchanged'] = True
        result['non_t04_table_digests'] = after['table_digests']
        if not args.apply:
            transaction.rollback()
    result['committed'] = args.apply
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in {
        'name_differences', 'source_count_mismatches', 'non_t04_table_digests'}}, ensure_ascii=True))
    engine.dispose()


if __name__ == '__main__':
    main()
