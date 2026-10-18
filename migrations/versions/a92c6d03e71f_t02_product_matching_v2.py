"""T02 representative products and coverage checkpoints.

Revision ID: a92c6d03e71f
Revises: 7b58d1f4b2a0
Create Date: 2026-10-18 22:40:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a92c6d03e71f"
down_revision: Union[str, Sequence[str], None] = "7b58d1f4b2a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "price_collection_runs",
        sa.Column("auto_adopted_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_collection_runs",
        sa.Column("estimate_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_collection_runs",
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_collection_runs",
        sa.Column("checkpoint_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_collection_runs",
        sa.Column("mapping_failure_rate", sa.Numeric(8, 6), nullable=True),
    )
    op.add_column(
        "price_collection_runs",
        sa.Column("price_update_success_rate", sa.Numeric(8, 6), nullable=True),
    )

    op.create_table(
        "retailer_product_mappings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("product_url", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("selection_reason", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_reassessment_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "canonical_name", name="uq_retailer_mapping_provider_ingredient"
        ),
    )
    op.create_index(
        "ix_retailer_product_mappings_product",
        "retailer_product_mappings",
        ["provider", "product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retailer_product_mappings_product",
        table_name="retailer_product_mappings",
    )
    op.drop_table("retailer_product_mappings")
    op.drop_column("price_collection_runs", "price_update_success_rate")
    op.drop_column("price_collection_runs", "mapping_failure_rate")
    op.drop_column("price_collection_runs", "checkpoint_count")
    op.drop_column("price_collection_runs", "missing_count")
    op.drop_column("price_collection_runs", "estimate_count")
    op.drop_column("price_collection_runs", "auto_adopted_count")
