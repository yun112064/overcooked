"""T11 adopted meal plans: minimal adopted-meal snapshot for previous-day dedup

已採用餐食紀錄的最小快照（家庭、餐食日期、餐別、模式、菜色識別與別名）。
T11 只讀取它做昨日排除；T16 以 ALTER 擴充採用交易、冪等與完整快照，不另建平行表。

Revision ID: a1c4e7f20b39
Revises: f3b7c1d9e204
Create Date: 2026-10-20 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1c4e7f20b39"
down_revision: Union[str, Sequence[str], None] = "f3b7c1d9e204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adopted_meal_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_date", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dishes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("meal_type IN ('breakfast', 'lunch', 'dinner')", name="ck_adopted_meal_type"),
        sa.CheckConstraint("mode IN ('database_menu', 'ai_generated_menu')", name="ck_adopted_meal_mode"),
        sa.CheckConstraint("status IN ('adopted', 'superseded')", name="ck_adopted_meal_status"),
        sa.CheckConstraint("version > 0", name="ck_adopted_meal_version_positive"),
    )
    op.create_index("ix_adopted_meal_plans_household_date", "adopted_meal_plans", ["household_id", "meal_date"])


def downgrade() -> None:
    op.drop_index("ix_adopted_meal_plans_household_date", table_name="adopted_meal_plans")
    op.drop_table("adopted_meal_plans")
