"""T15 meal result decisions and price-retry result versions

- meal_result_decisions：使用者對單一結果版本的決定（接受缺價例外、缺營養繼續查看、缺價重試），
  冪等鍵與 request_hash；T16 採用時重新讀取重驗。
- generation_results：缺價重試產生的新版本沒有工作，job_id 改為可為 NULL，
  新增 derived_from_result_id；兩者恰有一個（CHECK）。

Revision ID: c8e3f6a1d2b4
Revises: b5d2e8f41c07
Create Date: 2026-10-20 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8e3f6a1d2b4"
down_revision: Union[str, Sequence[str], None] = "b5d2e8f41c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generation_results", sa.Column(
        "derived_from_result_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=True))
    op.alter_column("generation_results", "job_id", nullable=True)
    op.create_check_constraint("ck_generation_result_origin", "generation_results",
                               "(job_id IS NULL) <> (derived_from_result_id IS NULL)")
    op.create_table(
        "meal_result_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_result_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("produced_result_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('accept_missing_price', 'continue_missing_nutrition', 'retry_missing_price')",
                           name="ck_meal_result_decision_kind"),
        sa.UniqueConstraint("household_id", "idempotency_key", name="uq_meal_result_decision_idempotency"),
        sa.UniqueConstraint("generation_result_id", "kind", name="uq_meal_result_decision_kind"),
    )
    op.create_index("ix_meal_result_decisions_household_id", "meal_result_decisions", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_meal_result_decisions_household_id", table_name="meal_result_decisions")
    op.drop_table("meal_result_decisions")
    # 重試產生的版本沒有工作，無法還原為 NOT NULL；先刪除
    op.execute("DELETE FROM generation_results WHERE job_id IS NULL")
    op.drop_constraint("ck_generation_result_origin", "generation_results", type_="check")
    op.alter_column("generation_results", "job_id", nullable=False)
    op.drop_column("generation_results", "derived_from_result_id")
