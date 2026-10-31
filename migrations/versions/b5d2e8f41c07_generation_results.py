"""T14 generation results: versioned, idempotent snapshot per successful generation job

每個成功的生成工作一筆結果；同一問卷的 result_version 依序遞增，job_id 唯一（冪等）。
T15 讀取顯示、T16 以 (meal_request_id, result_version) 採用。

Revision ID: b5d2e8f41c07
Revises: a1c4e7f20b39
Create Date: 2026-10-20 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b5d2e8f41c07"
down_revision: Union[str, Sequence[str], None] = "a1c4e7f20b39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("fallback_reason", sa.String(60), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('database_menu', 'ai_generated_menu')", name="ck_generation_result_mode"),
        sa.CheckConstraint("(mode = 'database_menu') = (fallback_reason IS NULL)",
                           name="ck_generation_result_fallback_reason"),
        sa.CheckConstraint("result_version > 0 AND request_version > 0", name="ck_generation_result_versions"),
        sa.UniqueConstraint("job_id", name="uq_generation_result_job"),
        sa.UniqueConstraint("meal_request_id", "result_version", name="uq_generation_result_version"),
    )
    op.create_index("ix_generation_results_household_id", "generation_results", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_results_household_id", table_name="generation_results")
    op.drop_table("generation_results")
