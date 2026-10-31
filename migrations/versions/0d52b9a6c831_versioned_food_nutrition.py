"""Preserve nutrition variants and their source observations.

Revision ID: 0d52b9a6c831
Revises: f3b7c1d9e204
"""
from alembic import op
import sqlalchemy as sa

revision = "0d52b9a6c831"
down_revision = "f3b7c1d9e204"
branch_labels = None
depends_on = None

NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugars_g", "sodium_mg", "dietary_fiber_g")


def upgrade():
    op.create_table(
        "nutrition_imports",
        sa.Column("import_id", sa.String(64), primary_key=True),
        sa.Column("source_files", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "nutrition_sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_sheet", sa.String(100), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "source_key", name="uq_nutrition_source_key"),
    )
    op.create_index("ix_nutrition_sources_import_id", "nutrition_sources", ["import_id"])
    op.create_table(
        "food_nutrition",
        sa.Column("nutrition_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("standard_name", sa.Text(), nullable=False),
        sa.Column("cooking_state", sa.Text(), nullable=False),
        sa.Column("cooking_method", sa.Text()),
        sa.Column("preparation", sa.Text()),
        sa.Column("calculation_method", sa.Text()),
        *(sa.Column(field, sa.Numeric(24, 12)) for field in NUTRIENTS),
        sa.Column("nutrition_complete", sa.Boolean(), nullable=False),
        sa.Column("quality_issues", sa.JSON(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "source_row", name="uq_food_nutrition_row"),
        *(sa.CheckConstraint(f"{field} IS NULL OR {field} >= 0", name=f"ck_food_nutrition_{field}") for field in NUTRIENTS),
    )
    op.create_index("ix_food_nutrition_import_id", "food_nutrition", ["import_id"])
    op.create_index("ix_food_nutrition_food_id", "food_nutrition", ["food_id"])
    op.create_table(
        "nutrition_source_links",
        sa.Column("nutrition_id", sa.String(64), sa.ForeignKey("food_nutrition.nutrition_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("nutrition_sources.source_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("included_in_calculation", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("nutrition_source_links")
    op.drop_table("food_nutrition")
    op.drop_table("nutrition_sources")
    op.drop_table("nutrition_imports")
