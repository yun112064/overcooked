"""Install verified package net masses into the dedicated local V3 database."""

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/t02_package_mass_20261104.json"


def install(connection, manifest):
    from sqlalchemy import text

    installed = 0
    for item in manifest["packages"]:
        package = {**item, "run_key": manifest["run_key"], "release": manifest["recipe_release_id"]}
        for field in ("volume_ml", "net_mass_g"):
            value = Decimal(package[field])
            if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -4:
                raise ValueError(f"Invalid {field}")
            package[field] = value
        source = urlsplit(package["source_url"])
        if source.scheme != "https" or not source.hostname or not package["source_detail"].strip():
            raise ValueError("Manufacturer evidence requires an HTTPS source and a locator")
        matched = connection.execute(text("""
            SELECT 1 FROM price_quote_snapshots s
            JOIN price_collection_runs r ON r.id = s.run_id
            JOIN recipe_data_releases a ON a.release_id = r.recipe_release_id AND a.active
            JOIN price_snapshot_foods f ON f.snapshot_id = s.id AND f.recipe_release_id = a.release_id
            WHERE s.id = :source_snapshot_id AND r.run_key = :run_key AND a.release_id = :release
              AND f.food_id = :food_id AND s.provider = :provider AND r.provider = :provider
              AND r.price_data_release_id IS NULL AND s.status = 'success'
              AND s.package_unit = 'ml' AND s.package_quantity = :volume_ml
              AND s.product_id = :product_id AND s.product_name = :product_name
        """), package).scalar_one_or_none()
        if matched is None:
            raise ValueError(f"Live source snapshot does not match {package['food_id']}")
        installed += connection.execute(text("""
            INSERT INTO price_package_mass_evidence
                (provider, product_id, product_name, volume_ml, net_mass_g, source_snapshot_id, source_url, source_detail)
            VALUES (:provider, :product_id, :product_name, :volume_ml, :net_mass_g,
                    :source_snapshot_id, :source_url, :source_detail)
            ON CONFLICT (provider, product_id, volume_ml) DO NOTHING
        """), package).rowcount
        verified = connection.execute(text("""
            SELECT 1 FROM price_package_mass_evidence WHERE provider = :provider AND product_id = :product_id
              AND volume_ml = :volume_ml AND product_name = :product_name AND net_mass_g = :net_mass_g
              AND source_snapshot_id = :source_snapshot_id AND source_url = :source_url AND source_detail = :source_detail
        """), package).scalar_one_or_none()
        if verified is None:
            raise ValueError(f"Conflicting package evidence for {package['product_id']}")
    return installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the verified evidence")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import settings
    from app.db.session import engine
    from update_t02_prices import preflight

    release = preflight(engine, settings.generation_recipe_release_id)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["recipe_release_id"] != release:
        raise ValueError("Package evidence manifest belongs to a different recipe release")
    with engine.connect() as connection, connection.begin() as transaction:
        installed = install(connection, manifest)
        if not args.apply:
            transaction.rollback()
    print(json.dumps({"checked": len(manifest["packages"]), "new_rows": installed, "committed": args.apply}))


if __name__ == "__main__":
    main()
