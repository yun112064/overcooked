"""Install the price-only historical release into dedicated V3 PostgreSQL."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]

from sqlalchemy import create_engine
from app.source_adapters.price_import import load_bundle, install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Commit; otherwise verify in a transaction and roll it back')
    args = parser.parse_args()
    from app.core.config import settings
    engine = create_engine(settings.database_url)
    if (engine.url.get_backend_name() != 'postgresql' or engine.url.database != 'meal_planner_1102_v3'
            or engine.url.host != '127.0.0.1' or engine.url.port != 55435):
        raise ValueError('Only meal_planner_1102_v3 is authorized for this CLI')
    bundle = load_bundle(args.manifest, args.sha256)
    with engine.connect() as connection:
        with connection.begin() as transaction:
            result = install(connection, bundle)
            if not args.apply:
                transaction.rollback()
    result['committed'] = args.apply
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'unresolved_bindings'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
