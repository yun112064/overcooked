"""3D 前端移植的資料表遷移 e1115demo3d01：不連資料庫，只用 Alembic 離線產生的 PostgreSQL SQL 檢查。

確認遷移鏈只有一個 head、只新增資料表（不 ALTER T01 與採用紀錄、不寫入資料）、
產生的欄位與 ORM 模型一致，且營養素欄位未知時保持 NULL（不設預設值）。
"""
import ast
import importlib.util
import io
import re
import sys
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.db.models  # noqa: E402,F401
import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.models import FoodNutrition, Recipe, RecipeIngredient  # noqa: E402

REVISION = "e1115demo3d01"
PARENT = "d4e8a2c7f913"
MIGRATION = ROOT / "migrations" / "versions" / "e1115demo3d01_demo_3d_port.py"

# 依外鍵順序建立；降版時必須反向刪除
TABLES = (
    "meal_menu_rejections",
    "recipe_data_releases",
    "nutrition_imports",
    "nutrition_sources",
    "food_nutrition",
    "nutrition_source_links",
    "nutrition_data_releases",
    "nutrition_product_sources",
    "nutrition_source_candidates",
    "recipe_covers",
)
INDEXES = {
    "ix_meal_menu_rejections_request": "meal_menu_rejections",
    "uq_recipe_data_releases_active": "recipe_data_releases",
    "ix_nutrition_sources_import_id": "nutrition_sources",
    "ix_food_nutrition_import_id": "food_nutrition",
    "ix_food_nutrition_food_id": "food_nutrition",
    "uq_nutrition_release_active": "nutrition_data_releases",
}
UNTOUCHED = ("recipes", "recipe_ingredients", "ingredient_aliases", "adopted_meal_plans")
NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugars_g", "sodium_mg", "dietary_fiber_g")


def _load_revision():
    spec = importlib.util.spec_from_file_location("e1115demo3d01_demo_3d_port", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(*steps: str) -> str:
    module = _load_revision()
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buf})
    with Operations.context(ctx):
        for step in steps:
            getattr(module, step)()
    return buf.getvalue()


def _create_bodies(sql: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.S)}


def _columns(body: str) -> tuple[set[str], set[str]]:
    columns, pk = set(), set()
    for line in body.splitlines():
        line = line.strip().rstrip(",").strip()
        if not line:
            continue
        match = re.match(r"PRIMARY KEY \((.*?)\)", line)
        if match:
            pk = {c.strip() for c in match.group(1).split(",")}
        elif not re.match(r"(CONSTRAINT|FOREIGN KEY|UNIQUE|CHECK)\b", line):
            columns.add(line.split()[0])
    return columns, pk


class RevisionChainTest(unittest.TestCase):
    def test_single_head_on_top_of_demo_head(self):
        script = ScriptDirectory(str(ROOT / "migrations"))
        self.assertEqual(script.get_heads(), [REVISION])
        self.assertEqual(script.get_revision(REVISION).down_revision, PARENT)


class OfflineRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upgrade_sql = _render("upgrade")
        cls.downgrade_sql = _render("upgrade", "downgrade")[len(cls.upgrade_sql):]
        cls.bodies = _create_bodies(cls.upgrade_sql)

    def test_creates_every_table_and_index_in_fk_order(self):
        created = re.findall(r"CREATE TABLE (\w+) \(", self.upgrade_sql)
        self.assertEqual(tuple(created), TABLES)
        for index, table in INDEXES.items():
            self.assertRegex(self.upgrade_sql, rf"CREATE (UNIQUE )?INDEX {index} ON {table} ")
        self.assertIn("CREATE UNIQUE INDEX uq_recipe_data_releases_active ON recipe_data_releases (active) WHERE active",
                      self.upgrade_sql)
        self.assertIn("row_counts JSONB DEFAULT '{}'::jsonb NOT NULL", self.bodies["recipe_data_releases"])
        self.assertIn("image_bytes BYTEA NOT NULL", self.bodies["recipe_covers"])

    def test_downgrade_drops_in_reverse_fk_order(self):
        dropped = re.findall(r"DROP TABLE (\w+);", self.downgrade_sql)
        self.assertEqual(tuple(dropped), tuple(reversed(TABLES)))
        self.assertEqual(set(re.findall(r"DROP INDEX (\w+);", self.downgrade_sql)), set(INDEXES))

    def test_rendered_columns_match_models(self):
        for table in TABLES:
            with self.subTest(table=table):
                columns, pk = _columns(self.bodies[table])
                model = Base.metadata.tables[table]
                self.assertEqual(columns, {c.name for c in model.columns})
                self.assertEqual(pk, {c.name for c in model.primary_key.columns})

    def test_rendered_indexes_match_models(self):
        model_indexes = {ix.name: ix.table.name for table in TABLES for ix in Base.metadata.tables[table].indexes}
        self.assertEqual(model_indexes, INDEXES)

    def test_additive_only_and_writes_no_data(self):
        sql = self.upgrade_sql + self.downgrade_sql
        self.assertNotRegex(sql, r"\bINSERT\b")
        self.assertNotRegex(sql, r"\bUPDATE\b")
        for table in UNTOUCHED:
            self.assertNotRegex(sql, rf"ALTER TABLE {table}\b")
            self.assertNotRegex(sql, rf"DROP TABLE {table}\b")
        for column in ("recipe_release_id", "volume_ml", "weight_basis", "servings_basis", "weight_evidence"):
            self.assertNotIn(column, Recipe.__table__.c)
            self.assertNotIn(column, RecipeIngredient.__table__.c)

    def test_recipe_covers_foreign_keys_are_demo_native(self):
        targets = {(fk.parent.name, fk.target_fullname, fk.ondelete)
                   for fk in Base.metadata.tables["recipe_covers"].foreign_keys}
        self.assertEqual(targets, {("recipe_id", "recipes.recipe_id", "CASCADE"),
                                   ("recipe_release_id", "recipe_data_releases.release_id", None)})
        self.assertIn("FOREIGN KEY(recipe_id) REFERENCES recipes (recipe_id) ON DELETE CASCADE",
                      self.bodies["recipe_covers"])
        self.assertIn("FOREIGN KEY(recipe_release_id) REFERENCES recipe_data_releases (release_id)",
                      self.bodies["recipe_covers"])


class UnknownNutritionStaysNullTest(unittest.TestCase):
    def test_nutrient_columns_nullable_without_defaults(self):
        table = FoodNutrition.__table__
        for name in NUTRIENTS:
            with self.subTest(column=name):
                column = table.c[name]
                self.assertTrue(column.nullable)
                self.assertIsNone(column.default)
                self.assertIsNone(column.server_default)

    def test_rendered_nutrient_columns_have_no_default(self):
        body = _create_bodies(_render("upgrade"))["food_nutrition"]
        for name in NUTRIENTS:
            with self.subTest(column=name):
                line = next(l.strip() for l in body.splitlines() if l.strip().startswith(name + " "))
                self.assertEqual(line.rstrip(","), f"{name} NUMERIC(24, 12)")


class OfflineSafeRevisionTest(unittest.TestCase):
    def test_revision_has_no_bind_or_inspection(self):
        text = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("get_bind", text)
        self.assertNotRegex(text, r"\binspect\b")
        tree = ast.parse(text)
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {"get_bind", "inspect", "execute"})
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in ("upgrade", "downgrade"):
            self.assertFalse([n for n in ast.walk(functions[name]) if isinstance(n, ast.If)],
                             f"{name}() 不可有條件判斷")


if __name__ == "__main__":
    unittest.main()
