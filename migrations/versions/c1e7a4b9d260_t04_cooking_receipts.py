"""Persist exact T04 cooking receipts for versioned T12.

Revision ID: c1e7a4b9d260
Revises: 9c3d7f1a2b50
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1e7a4b9d260"
down_revision = "9c3d7f1a2b50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t04_cooking_receipts",
        sa.Column("receipt_id", sa.String(64), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64), sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("nutrition_import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mapping_version", sa.String(100), nullable=False),
        sa.Column("recipe_ingredient_id", sa.String(32), nullable=False),
        sa.Column("food_id", sa.String(32), nullable=False),
        sa.Column("nutrition_id", sa.String(64), sa.ForeignKey("food_nutrition.nutrition_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("nutrition_source_id", sa.String(64), sa.ForeignKey("nutrition_sources.source_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sample_id", sa.String(100), nullable=False),
        sa.Column("cooking_state", sa.Text(), nullable=False),
        sa.Column("cooking_method", sa.Text(), nullable=False),
        sa.Column("weight_basis", sa.String(12), nullable=False),
        sa.Column("weight_g", sa.Numeric(16, 6), nullable=False),
        sa.Column("yield_factor", sa.Numeric(10, 6), nullable=False),
        sa.Column("retention", postgresql.JSONB(), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("is_estimated", sa.Boolean(), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False),
        sa.Column("reference_ids", postgresql.JSONB(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("input_digest", "source_version", name="uq_t04_receipt_input_version"),
        sa.CheckConstraint("weight_basis IN ('raw','cooked')", name="ck_t04_receipt_weight_basis"),
        sa.CheckConstraint("weight_g > 0", name="ck_t04_receipt_weight_positive"),
        sa.CheckConstraint("yield_factor > 0 AND yield_factor <= 10", name="ck_t04_receipt_yield"),
    )
    op.create_index("ix_t04_receipt_lookup", "t04_cooking_receipts",
                    ["recipe_release_id", "nutrition_import_id", "mapping_version", "recipe_ingredient_id"])


def downgrade() -> None:
    op.drop_index("ix_t04_receipt_lookup", table_name="t04_cooking_receipts")
    op.drop_table("t04_cooking_receipts")
