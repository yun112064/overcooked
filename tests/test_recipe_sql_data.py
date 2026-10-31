"""Verify that a fresh PostgreSQL database can install the recipe SQL release."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from sqlalchemy import create_engine, text

from db_support import ROOT, TEST_DATABASE_URL, migrate_fresh, requires_db


@requires_db
class RecipeSqlDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        migrate_fresh()
        cls.engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _bootstrap(self) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bootstrap_recipe_data.py"),
                "--database-url",
                TEST_DATABASE_URL,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_00_seed_is_idempotent_and_reconciled(self) -> None:
        first = self._bootstrap()
        self.assertEqual("installed", first["status"])
        self.assertEqual(293, first["counts"]["recipes"])
        self.assertEqual(1471, first["counts"]["recipe_steps"])
        self.assertEqual(2403, first["counts"]["recipe_ingredients"])

        second = self._bootstrap()
        self.assertEqual("already_installed", second["status"])

        with self.engine.connect() as connection:
            active = connection.execute(
                text("SELECT release_id FROM recipe_data_releases WHERE active")
            ).scalar_one()
            used_food_ids = connection.execute(
                text(
                    "SELECT count(DISTINCT food_id) FROM recipe_ingredients "
                    "WHERE recipe_release_id = :release_id"
                ),
                {"release_id": active},
            ).scalar_one()
            old_step_ids = connection.execute(
                text(
                    "SELECT count(*) FROM recipe_steps "
                    "WHERE recipe_release_id = :release_id AND step_id NOT LIKE 'RS%'"
                ),
                {"release_id": active},
            ).scalar_one()

        self.assertEqual("recipe-1027-e61bbd81", active)
        self.assertEqual(434, used_food_ids)
        self.assertEqual(0, old_step_ids)

    def test_price_collection_scope_comes_from_active_release(self) -> None:
        self._bootstrap()
        from app.source_adapters.ingredient_database import load_ingredient_queries

        queries = load_ingredient_queries(self.engine)
        food_ids = {food_id for query in queries for food_id in query.food_ids}
        self.assertEqual(434, len(food_ids))
        self.assertTrue(all(query.canonical_name for query in queries))

    def test_feature_mappings_only_reference_active_food_ids(self) -> None:
        self._bootstrap()
        with self.engine.connect() as connection:
            active_food_ids = set(
                connection.execute(
                    text(
                        "SELECT f.food_id FROM ingredient_aliases f "
                        "JOIN recipe_data_releases r ON r.release_id = f.recipe_release_id "
                        "WHERE r.active"
                    )
                ).scalars()
            )

        recognition = json.loads(
            (ROOT / "config" / "recognition_labels.json").read_text(encoding="utf-8")
        )
        recognition_ids = {
            item["ingredient_id"]
            for item in recognition["labels"].values()
            if item.get("ingredient_id")
        }
        synonyms = json.loads(
            (ROOT / "config" / "ingredient_synonyms.json").read_text(encoding="utf-8")
        )
        synonym_ids = {
            food["food_id"]
            for group in synonyms["groups"]
            for food in group["foods"]
        }

        self.assertEqual(set(), recognition_ids - active_food_ids)
        self.assertEqual(set(), synonym_ids - active_food_ids)

    def test_reviewed_spicy_dish_keeps_distinct_identity(self) -> None:
        self._bootstrap()
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT recipe_id, category, standard_dish_id, name_aliases "
                    "FROM recipes WHERE recipe_id IN ('R0180', 'R0182')"
                )
            ).mappings().all()
        recipes = {row["recipe_id"]: row for row in rows}
        spicy, plain = recipes["R0180"], recipes["R0182"]
        self.assertEqual("主菜", spicy["category"])
        self.assertEqual("主菜", plain["category"])
        self.assertNotEqual(spicy["standard_dish_id"], plain["standard_dish_id"])
        self.assertNotIn("桂竹筍炒肉絲", spicy["name_aliases"])


if __name__ == "__main__":
    unittest.main()
