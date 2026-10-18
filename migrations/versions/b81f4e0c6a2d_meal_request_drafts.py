"""T08 meal request drafts and revisions

Revision ID: b81f4e0c6a2d
Revises: a7e3c2d91b04
Create Date: 2026-10-18 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b81f4e0c6a2d"
down_revision: Union[str, Sequence[str], None] = "a7e3c2d91b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meal_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("meal_date", sa.Date(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'awaiting_decision', 'result_ready', "
            "'failed', 'confirmed')", name="ck_meal_request_status"),
    )
    op.create_index("ix_meal_requests_household_id", "meal_requests", ["household_id"])

    op.create_table(
        "meal_request_revisions",
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("meal_request_revisions")
    op.drop_index("ix_meal_requests_household_id", table_name="meal_requests")
    op.drop_table("meal_requests")
