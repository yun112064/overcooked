"""Add versioned Food_ID to TFDA mapping candidates and review status.

Revision ID: 7a2c9e4d1b60
Revises: f1a8c4d2e709
"""
from alembic import op
import sqlalchemy as sa

revision = "7a2c9e4d1b60"
down_revision = "f1a8c4d2e709"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nutrition_food_mappings",
        sa.Column("mapping_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipe_release_id", sa.String(64), sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("nutrition_id", sa.String(64), sa.ForeignKey("food_nutrition.nutrition_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("nutrition_sources.source_id", ondelete="CASCADE"), nullable=False),
        sa.Column("cooking_state", sa.Text(), nullable=False),
        sa.Column("cooking_method", sa.Text()),
        sa.Column("preparation", sa.Text()),
        sa.Column("approval_status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("mapping_version", sa.String(100), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "food_id", "nutrition_id", "source_id", name="uq_nutrition_food_mapping"),
        sa.CheckConstraint("approval_status IN ('pending_review','approved','rejected')",
                           name="ck_nutrition_food_mapping_status"),
    )
    op.create_index("ix_nutrition_food_mappings_import_id", "nutrition_food_mappings", ["import_id"])
    op.create_index("ix_nutrition_food_mappings_recipe_release_id", "nutrition_food_mappings", ["recipe_release_id"])
    op.create_index("ix_nutrition_food_mappings_food_id", "nutrition_food_mappings", ["food_id"])


def downgrade():
    op.drop_index("ix_nutrition_food_mappings_food_id", table_name="nutrition_food_mappings")
    op.drop_index("ix_nutrition_food_mappings_recipe_release_id", table_name="nutrition_food_mappings")
    op.drop_index("ix_nutrition_food_mappings_import_id", table_name="nutrition_food_mappings")
    op.drop_table("nutrition_food_mappings")
