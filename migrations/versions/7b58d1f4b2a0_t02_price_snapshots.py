"""T02 price collection runs and append-only quote snapshots.

Revision ID: 7b58d1f4b2a0
Revises: c4afb1c5751e
Create Date: 2026-10-18 22:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "7b58d1f4b2a0"
down_revision: Union[str, Sequence[str], None] = "c4afb1c5751e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_collection_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ingredient_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "status_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "missing_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("fatal_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_price_collection_runs_started_at",
        "price_collection_runs",
        ["started_at"],
    )

    op.create_table(
        "price_quote_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("price_collection_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("missing_reason", sa.Text(), nullable=True),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_id", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(14, 4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("package_quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("package_unit", sa.String(length=16), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "quote_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id", "canonical_name", name="uq_price_snapshot_run_ingredient"
        ),
    )
    op.create_index(
        "ix_price_quote_snapshots_ingredient_time",
        "price_quote_snapshots",
        ["canonical_name", "queried_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_price_quote_snapshots_ingredient_time",
        table_name="price_quote_snapshots",
    )
    op.drop_table("price_quote_snapshots")
    op.drop_index(
        "ix_price_collection_runs_started_at",
        table_name="price_collection_runs",
    )
    op.drop_table("price_collection_runs")
