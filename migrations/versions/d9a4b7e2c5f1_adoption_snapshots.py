"""T16 adoption transaction: extend adopted_meal_plans with versions, idempotency and full snapshot

以 ALTER 擴充 T11 的 adopted_meal_plans（不另建平行表）：
- meal_request_id／generation_result_id／result_version／request_version：採用的問卷與結果版本。
  問卷或結果之後被刪除時改為 NULL，已採用歷史與快照保留。
- idempotency_key＋request_hash：(household_id, idempotency_key) 唯一；同一問卷只採用一次（meal_request_id 唯一）。
- snapshot：MealPlanSnapshot（配方、參與項目、目標、來源／規則版本、缺值標示、知識引用、昨日比對）。
- price_exception_accepted：當次是否以缺價例外採用。adopted_by：採用者。
T11 寫入的最小紀錄這些欄位維持 NULL／預設值。

Revision ID: d9a4b7e2c5f1
Revises: c8e3f6a1d2b4
Create Date: 2026-10-20 23:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d9a4b7e2c5f1"
down_revision: Union[str, Sequence[str], None] = "c8e3f6a1d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "adopted_meal_plans"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                                   sa.ForeignKey("meal_requests.id", ondelete="SET NULL",
                                                 name="fk_adopted_meal_plans_meal_request"), nullable=True))
    op.add_column(TABLE, sa.Column("generation_result_id", postgresql.UUID(as_uuid=True),
                                   sa.ForeignKey("generation_results.id", ondelete="SET NULL",
                                                 name="fk_adopted_meal_plans_generation_result"), nullable=True))
    op.add_column(TABLE, sa.Column("result_version", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("request_version", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column(TABLE, sa.Column("request_hash", sa.String(64), nullable=True))
    op.add_column(TABLE, sa.Column("price_exception_accepted", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    op.add_column(TABLE, sa.Column("snapshot", postgresql.JSONB(), nullable=False,
                                   server_default=sa.text("'{}'::jsonb")))
    op.add_column(TABLE, sa.Column("adopted_by", postgresql.UUID(as_uuid=True),
                                   sa.ForeignKey("users.id", ondelete="SET NULL",
                                                 name="fk_adopted_meal_plans_adopted_by"), nullable=True))
    op.create_unique_constraint("uq_adopted_meal_plan_idempotency", TABLE, ["household_id", "idempotency_key"])
    op.create_unique_constraint("uq_adopted_meal_plan_request", TABLE, ["meal_request_id"])
    op.create_check_constraint("ck_adopted_meal_idempotency_pair", TABLE,
                               "(idempotency_key IS NULL) = (request_hash IS NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_adopted_meal_idempotency_pair", TABLE, type_="check")
    op.drop_constraint("uq_adopted_meal_plan_request", TABLE, type_="unique")
    op.drop_constraint("uq_adopted_meal_plan_idempotency", TABLE, type_="unique")
    for column in ("adopted_by", "snapshot", "price_exception_accepted", "request_hash", "idempotency_key",
                   "request_version", "result_version", "generation_result_id", "meal_request_id"):
        op.drop_column(TABLE, column)
