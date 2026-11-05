"""Scope immutable TFDA mapping versions to their recipe release.

Revision ID: 9c3d7f1a2b50
Revises: 8b3e1f6a9c24
"""
from alembic import op

revision = "9c3d7f1a2b50"
down_revision = "8b3e1f6a9c24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings", type_="unique")
    op.create_unique_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings",
        ["import_id", "recipe_release_id", "food_id", "nutrition_id", "source_id", "mapping_version"])


def downgrade() -> None:
    op.drop_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings", type_="unique")
    op.create_unique_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings",
        ["import_id", "food_id", "nutrition_id", "source_id", "mapping_version"])
