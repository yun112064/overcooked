"""Capture, preflight or apply the V3 management adoption with an explicit PostgreSQL DSN."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.management.adopt_v3_v1 import (  # noqa: E402
    SOURCE_DB, adopt, capture_baseline, validate_manifest, validate_target_name,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "preflight", "apply"))
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--target")
    parser.add_argument("--approval")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        raw_url = os.environ.get("V3_ADOPTION_DATABASE_URL")
        if not raw_url:
            raise ValueError("Set the explicit V3_ADOPTION_DATABASE_URL environment variable")
        url = sa.make_url(raw_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("Expected PostgreSQL")
        with args.backup.open("rb") as backup:
            backup_sha256 = hashlib.file_digest(backup, "sha256").hexdigest()
        if args.mode == "capture":
            if url.database != SOURCE_DB or not args.target or not args.approval or args.manifest:
                raise ValueError("Capture requires the V3 source DSN, --target and --approval")
            validate_target_name(args.target)
            manifest = None
        else:
            if not args.manifest or args.target or args.approval:
                raise ValueError("Preflight and apply require --manifest")
            validate_target_name(url.database)
            manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
            validate_manifest(manifest, backup_sha256)
            if url.database != manifest["target_database"]:
                raise ValueError("Database URL and manifest target do not match")

        # Alembic is given this exact transaction. Its environment requires a settings URL.
        os.environ.setdefault("DATABASE_URL", "postgresql://")
        engine = sa.create_engine(url, hide_parameters=True)
        try:
            with engine.begin() as c:
                if args.mode != "apply":
                    c.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                result = (capture_baseline(c, args.target, args.approval, backup_sha256)
                          if manifest is None else adopt(c, manifest, backup_sha256, apply=args.mode == "apply"))
        finally:
            engine.dispose()
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, sa.exc.SQLAlchemyError) as error:
        print(f"Adoption stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
