"""Install the immutable recipe SQL release after Alembic migrations.

This command never reads Excel. It verifies the committed compressed SQL seed,
installs it transactionally and then checks row counts and foreign-key coverage.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "recipe_data_manifest.json"


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    command.add_argument("--force", action="store_true", help="Reapply the same verified SQL release")
    return command


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(connection, manifest: dict) -> dict[str, int]:
    release_id = manifest["release_id"]
    expected = manifest["counts"]
    queries = {
        "source_archive": (
            "SELECT count(*) FROM source_archive WHERE recipe_release_id = :release_id AND import_batch_id = :source_sha",
            {"release_id": release_id, "source_sha": manifest["source_sha256"]},
        ),
        "recipes": (
            "SELECT count(*) FROM recipes WHERE recipe_release_id = :release_id",
            {"release_id": release_id},
        ),
        "recipe_steps": (
            "SELECT count(*) FROM recipe_steps WHERE recipe_release_id = :release_id",
            {"release_id": release_id},
        ),
        "recipe_ingredients": (
            "SELECT count(*) FROM recipe_ingredients WHERE recipe_release_id = :release_id",
            {"release_id": release_id},
        ),
        "ingredient_aliases": (
            "SELECT count(*) FROM ingredient_aliases WHERE recipe_release_id = :release_id",
            {"release_id": release_id},
        ),
        "allergens": (
            "SELECT count(*) FROM allergens WHERE recipe_release_id = :release_id",
            {"release_id": release_id},
        ),
    }
    actual = {
        name: connection.execute(text(sql), params).scalar_one()
        for name, (sql, params) in queries.items()
    }
    if actual != expected:
        raise RuntimeError(f"Recipe SQL reconciliation failed: expected={expected}, actual={actual}")

    orphan_checks = {
        "step_recipe": """
            SELECT count(*) FROM recipe_steps s
            LEFT JOIN recipes r ON r.recipe_id = s.recipe_id
            WHERE r.recipe_id IS NULL
        """,
        "ingredient_recipe": """
            SELECT count(*) FROM recipe_ingredients i
            LEFT JOIN recipes r ON r.recipe_id = i.recipe_id
            WHERE r.recipe_id IS NULL
        """,
        "ingredient_food": """
            SELECT count(*) FROM recipe_ingredients i
            LEFT JOIN ingredient_aliases f ON f.food_id = i.food_id
            WHERE f.food_id IS NULL OR f.recipe_release_id <> i.recipe_release_id
        """,
        "release_mismatch": """
            SELECT count(*) FROM recipes r
            WHERE r.recipe_release_id = :release_id AND (
                EXISTS (
                    SELECT 1 FROM recipe_steps s
                    WHERE s.recipe_id = r.recipe_id
                      AND s.recipe_release_id <> r.recipe_release_id
                ) OR EXISTS (
                    SELECT 1 FROM recipe_ingredients i
                    WHERE i.recipe_id = r.recipe_id
                      AND i.recipe_release_id <> r.recipe_release_id
                )
            )
        """,
    }
    problems = {
        name: connection.execute(
            text(sql), {"release_id": release_id}
        ).scalar_one()
        for name, sql in orphan_checks.items()
    }
    if any(problems.values()):
        raise RuntimeError(f"Recipe SQL foreign-key reconciliation failed: {problems}")
    active = connection.execute(
        text("SELECT release_id FROM recipe_data_releases WHERE active")
    ).scalars().all()
    if active != [release_id]:
        raise RuntimeError(f"Unexpected active recipe release: {active}")
    return actual


def main() -> int:
    args = parser().parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed_path = manifest_path.parent / manifest["seed_file"]
    if not seed_path.is_file():
        raise SystemExit(f"Recipe SQL seed not found: {seed_path}")
    actual_sha = sha256(seed_path)
    if actual_sha != manifest["seed_sha256"]:
        raise SystemExit(
            f"Recipe SQL seed checksum mismatch: expected {manifest['seed_sha256']}, got {actual_sha}"
        )

    engine = create_engine(args.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            table_exists = connection.execute(
                text("SELECT to_regclass('public.recipe_data_releases') IS NOT NULL")
            ).scalar_one()
            if not table_exists:
                raise SystemExit("Run 'alembic upgrade head' before installing recipe data")
            installed = connection.execute(
                text(
                    "SELECT active FROM recipe_data_releases WHERE release_id = :release_id"
                ),
                {"release_id": manifest["release_id"]},
            ).scalar_one_or_none()
            if installed and not args.force:
                counts = validate(connection, manifest)
                print(json.dumps({"status": "already_installed", "counts": counts}, ensure_ascii=False))
                return 0

        sql = gzip.decompress(seed_path.read_bytes()).decode("utf-8")
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute(sql)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

        with engine.connect() as connection:
            counts = validate(connection, manifest)
        print(
            json.dumps(
                {
                    "status": "installed",
                    "release_id": manifest["release_id"],
                    "source_sha256": manifest["source_sha256"],
                    "counts": counts,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
