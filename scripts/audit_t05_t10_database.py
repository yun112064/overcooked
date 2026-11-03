"""Read-only T05–T10 connection, dictionary and runtime-table inspection on V3."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings
from app.db.session import engine
from app.db.models import (BackgroundJob, BudgetMonth, Household,
                           HouseholdMembership, MealRequest, MealRequestRevision,
                           Member, Recognition, UsageReservation, User, UserSession)
from app.services import budget_floor, ingredients


def audit() -> dict:
    target = engine.url
    if (target.database, target.host, target.port) != (
        "meal_planner_1102_v3", "127.0.0.1", 55435
    ):
        raise ValueError("Inspection requires the dedicated local V3 database")
    labels = json.loads(settings.recognition_labels_path.read_text(encoding="utf-8"))
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        if connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise ValueError("Read-only transaction was not established")
        result = {
            "checked_at_taipei": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            "database": connection.scalar(text("SELECT current_database()")),
            "host": target.host, "port": target.port,
            "transaction_read_only": True, "database_write_operations": 0,
            "head": connection.scalar(text("SELECT version_num FROM alembic_version")),
            "active_recipe_releases": [dict(r) for r in connection.execute(text(
                "SELECT release_id,dataset_version FROM recipe_data_releases WHERE active"
            )).mappings()],
            "dictionary_release_counts": [dict(r) for r in connection.execute(text(
                "SELECT recipe_release_id,count(*) AS count FROM ingredient_aliases GROUP BY recipe_release_id"
            )).mappings()],
        }
        foods = dict(connection.execute(text("SELECT food_id,canonical_name FROM ingredient_aliases")).all())
        assigned = [dict(label=key, display_name=value["name"], food_id=value["ingredient_id"],
                         database_name=foods.get(value["ingredient_id"]))
                    for key, value in labels["labels"].items() if value.get("ingredient_id")]
        result["recognition_labels"] = {
            "version": labels["labels_version"], "total": len(labels["labels"]),
            "assigned": len(assigned), "unassigned": len(labels["labels"]) - len(assigned),
            "missing_ids": [r for r in assigned if r["database_name"] is None],
            "different_display_names": [r for r in assigned if r["database_name"] is not None
                                        and r["database_name"] != r["display_name"]],
        }
        models = (User, UserSession, Household, HouseholdMembership, Member,
                  MealRequest, MealRequestRevision, Recognition,
                  BackgroundJob, BudgetMonth, UsageReservation)
        result["runtime_counts"] = {
            model.__tablename__: connection.scalar(text(f'SELECT count(*) FROM "{model.__tablename__}"'))
            for model in models
        }
        with Session(bind=connection) as db:
            result["latest_food_id_lookup"] = ingredients.describe(db, ["FOOD001993"])
            result["latest_food_id_unknown"] = ingredients.unknown_ids(db, ["FOOD001993"])
            result["ingredient_search_sample"] = ingredients.search(db, "高麗菜", 3)
            result["recognition_confirmation_missing_ids"] = ingredients.unknown_ids(
                db, [r["food_id"] for r in assigned])
            # Exercise the same read shapes as T07–T10 without exposing user fields.
            result["runtime_orm_selects"] = {
                model.__tablename__: "PASS"
                for model in models
                if db.execute(select(model).limit(0)).all() == []
            }
            floors = budget_floor._recipe_floors(db)
            batch_ids = set(connection.execute(text(
                "SELECT recipe_id FROM recipes WHERE servings_basis='batch' AND is_eligible"
            )).scalars())
            result["questionnaire_budget_reads"] = {
                "recipe_floors_loaded": len(floors),
                "usable_current_unit_prices": len(budget_floor._unit_prices(db, datetime.now(timezone.utc))),
                "batch_recipe_ids_in_per_n_floors": [f.recipe_id for f in floors if f.recipe_id in batch_ids],
                "basis_review_needed": bool(batch_ids.intersection(f.recipe_id for f in floors)),
            }
        result["scope"] = "No services started, no login/photo/job writes, no crawlers or models"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        default=ROOT / "docs/1102_V3/T05-T10/database-read-audit.json")
    args = parser.parse_args()
    try:
        result = audit()
    finally:
        engine.dispose()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
