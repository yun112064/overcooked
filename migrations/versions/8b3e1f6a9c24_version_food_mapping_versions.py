"""Allow multiple immutable Food_ID mapping versions for one nutrition import.

Revision ID: 8b3e1f6a9c24
Revises: 7a2c9e4d1b60
"""
from alembic import op
import sqlalchemy as sa

revision = "8b3e1f6a9c24"
down_revision = "7a2c9e4d1b60"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings", type_="unique")
    op.create_unique_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings",
        ["import_id", "food_id", "nutrition_id", "source_id", "mapping_version"])


def downgrade():
    op.drop_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings", type_="unique")
    op.create_unique_constraint("uq_nutrition_food_mapping", "nutrition_food_mappings",
        ["import_id", "food_id", "nutrition_id", "source_id"])
