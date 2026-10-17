"""Add isolated PX Mart / Carrefour retail price storage.

Revision ID: 7b2c90e4f1a6
Revises: 3d4a4d3b0e61
Create Date: 2026-10-17

This migration creates four NEW tables. It does not modify recipe tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "7b2c90e4f1a6"
down_revision = "3d4a4d3b0e61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retail_products",
        sa.Column("product_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("retailer", sa.String(20), nullable=False),
        sa.Column("source_product_id", sa.String(150), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("package_weight_g", sa.Numeric(18, 6), nullable=True),
        sa.Column("package_description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("retailer IN ('pxmart', 'carrefour')", name="ck_retail_products_retailer"),
        sa.CheckConstraint("package_weight_g > 0", name="ck_retail_products_weight_positive"),
        sa.UniqueConstraint("retailer", "source_product_id", name="uq_retail_products_source"),
    )
    op.create_index("ix_retail_products_name", "retail_products", ["product_name"])

    op.create_table(
        "retail_product_ingredients",
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("retail_products.product_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", sa.String(32), nullable=False),
        sa.Column("match_method", sa.String(20), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("product_id", "ingredient_id", name="pk_retail_product_ingredients"),
        sa.CheckConstraint("match_method IN ('exact', 'alias', 'llm', 'manual')", name="ck_retail_ingredients_method"),
    )
    # The existing recipe schema has no canonical ingredient master table:
    # deliberately no FK on ingredient_id until a stable master key exists.
    op.create_index("ix_retail_product_ingredients_ingredient_id", "retail_product_ingredients", ["ingredient_id"])

    op.create_table(
        "retail_price_update_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("retailer", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("retailer IN ('pxmart', 'carrefour')", name="ck_retail_runs_retailer"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="ck_retail_runs_status"),
        sa.CheckConstraint("fetched_count >= 0 AND inserted_count >= 0 AND skipped_count >= 0", name="ck_retail_runs_counts"),
    )
    op.create_index("ix_retail_runs_started", "retail_price_update_runs", ["retailer", "started_at"])

    op.create_table(
        "retail_price_snapshots",
        sa.Column("snapshot_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("retail_products.product_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("retail_price_update_runs.run_id", ondelete="SET NULL"), nullable=True),
        sa.Column("package_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="TWD"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("promotion_text", sa.Text(), nullable=True),
        sa.Column("source_method", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("package_price >= 0", name="ck_retail_snapshots_price_nonnegative"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > observed_at", name="ck_retail_snapshots_validity"),
    )
    op.create_index("ix_retail_snapshots_product_observed", "retail_price_snapshots", ["product_id", "observed_at"])
    op.create_index("ix_retail_snapshots_run", "retail_price_snapshots", ["run_id"])


def downgrade() -> None:
    # Downgrade destroys retail pricing data. Back up before using it.
    op.drop_index("ix_retail_snapshots_run", table_name="retail_price_snapshots")
    op.drop_index("ix_retail_snapshots_product_observed", table_name="retail_price_snapshots")
    op.drop_table("retail_price_snapshots")
    op.drop_index("ix_retail_runs_started", table_name="retail_price_update_runs")
    op.drop_table("retail_price_update_runs")
    op.drop_index("ix_retail_product_ingredients_ingredient_id", table_name="retail_product_ingredients")
    op.drop_table("retail_product_ingredients")
    op.drop_index("ix_retail_products_name", table_name="retail_products")
    op.drop_table("retail_products")
