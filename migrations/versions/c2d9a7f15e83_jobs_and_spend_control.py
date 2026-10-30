"""T10 durable jobs and spend control

Revision ID: c2d9a7f15e83
Revises: b81f4e0c6a2d
Create Date: 2026-10-18 19:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c2d9a7f15e83"
down_revision: Union[str, Sequence[str], None] = "b81f4e0c6a2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("household_id", UUID, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("meal_request_id", UUID, sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=True),
        sa.Column("input_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", TS, nullable=False),
        sa.Column("lease_owner", sa.String(100), nullable=True),
        sa.Column("lease_expires_at", TS, nullable=True),
        sa.Column("stage", sa.String(40), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_job_status"),
        sa.UniqueConstraint("household_id", "idempotency_key", name="uq_job_idempotency"),
    )
    op.create_index("ix_background_jobs_household_id", "background_jobs", ["household_id"])
    op.create_index("ix_background_jobs_meal_request_id", "background_jobs", ["meal_request_id"])
    # 工作程序領取：只掃描排隊中且到期的工作
    op.create_index("ix_background_jobs_claim", "background_jobs", ["available_at"],
                    postgresql_where=sa.text("status = 'queued'"))

    op.create_table(
        "budget_months",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("limit_twd", sa.Numeric(12, 4), nullable=False),
        sa.Column("committed_twd", sa.Numeric(12, 4), nullable=False),
        sa.Column("reserved_twd", sa.Numeric(12, 4), nullable=False),
        sa.Column("spent_twd", sa.Numeric(12, 4), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("reserved_twd >= 0 AND spent_twd >= 0 AND committed_twd >= 0",
                           name="ck_budget_nonnegative"),
    )

    op.create_table(
        "usage_reservations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("call_key", sa.String(150), nullable=False, unique=True),
        sa.Column("job_id", UUID, sa.ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("household_id", UUID, sa.ForeignKey("households.id", ondelete="SET NULL"), nullable=True),
        sa.Column("month", sa.String(7), sa.ForeignKey("budget_months.month"), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_twd", sa.Numeric(12, 4), nullable=False),
        sa.Column("actual_twd", sa.Numeric(12, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("fx_rate_twd", sa.Numeric(10, 4), nullable=False),
        sa.Column("pricing_version", sa.String(40), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("settled_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('reserved', 'settled', 'released', 'uncertain')", name="ck_usage_status"),
    )
    op.create_index("ix_usage_reservations_job_id", "usage_reservations", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_reservations_job_id", table_name="usage_reservations")
    op.drop_table("usage_reservations")
    op.drop_table("budget_months")
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.drop_index("ix_background_jobs_meal_request_id", table_name="background_jobs")
    op.drop_index("ix_background_jobs_household_id", table_name="background_jobs")
    op.drop_table("background_jobs")
