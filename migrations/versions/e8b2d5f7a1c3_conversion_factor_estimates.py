"""decisions #19 conversion factor estimates (cooking yield/retention, grams per unit)

缺烹調換算（產率／保留率）與容量單位克數時，事先以批次指令推估並快取；生成時只查表、不即時呼叫 LLM。
每列一個推估或人工值：kind=cooking 以 (food_id, 正規化烹調方式) 為鍵，kind=unit 以 (food_id, 原單位) 為鍵。
同一鍵可有多列（重新推估、人工覆寫皆新增一列保留紀錄）；取用優先順序：人工核可／覆寫 > LLM 推估，
rejected 永不使用（TFDA／USDA 實測不入此表，計算時永遠優先）。

Revision ID: e8b2d5f7a1c3
Revises: a3d6e9b1c4f7
Create Date: 2026-10-20 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e8b2d5f7a1c3"
down_revision: Union[str, Sequence[str], None] = "a3d6e9b1c4f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversion_factor_estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("food_id", sa.String(32), nullable=False),
        sa.Column("factor_key", sa.String(100), nullable=False),
        sa.Column("food_name", sa.Text(), nullable=True),
        sa.Column("yield_factor", sa.Numeric(10, 4), nullable=True),
        sa.Column("energy_retention", sa.Numeric(6, 4), nullable=True),
        sa.Column("protein_retention", sa.Numeric(6, 4), nullable=True),
        sa.Column("grams_per_unit", sa.Numeric(12, 4), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("call_key", sa.String(200), nullable=True),
        sa.Column("reviewer", sa.String(100), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('cooking', 'unit')", name="ck_conversion_factor_kind"),
        sa.CheckConstraint("source IN ('tfda', 'human', 'llm_estimated')", name="ck_conversion_factor_source"),
        sa.CheckConstraint("status IN ('llm_estimated', 'approved', 'rejected', 'overridden')",
                           name="ck_conversion_factor_status"),
        sa.CheckConstraint(
            "(kind = 'cooking' AND grams_per_unit IS NULL AND energy_retention IS NOT NULL "
            "AND protein_retention IS NOT NULL) OR "
            "(kind = 'unit' AND grams_per_unit IS NOT NULL AND yield_factor IS NULL "
            "AND energy_retention IS NULL AND protein_retention IS NULL)",
            name="ck_conversion_factor_values"),
        sa.CheckConstraint("yield_factor IS NULL OR (yield_factor > 0 AND yield_factor <= 10)",
                           name="ck_conversion_factor_yield_range"),
        sa.CheckConstraint("energy_retention IS NULL OR (energy_retention >= 0 AND energy_retention <= 1)",
                           name="ck_conversion_factor_energy_retention_range"),
        sa.CheckConstraint("protein_retention IS NULL OR (protein_retention >= 0 AND protein_retention <= 1)",
                           name="ck_conversion_factor_protein_retention_range"),
        sa.CheckConstraint("grams_per_unit IS NULL OR grams_per_unit > 0", name="ck_conversion_factor_grams_positive"),
        sa.CheckConstraint("source <> 'llm_estimated' OR (model IS NOT NULL AND prompt_version IS NOT NULL "
                           "AND schema_version IS NOT NULL)", name="ck_conversion_factor_llm_trace"),
    )
    op.create_index("ix_conversion_factor_estimates_key", "conversion_factor_estimates",
                    ["kind", "food_id", "factor_key"])


def downgrade() -> None:
    op.drop_index("ix_conversion_factor_estimates_key", table_name="conversion_factor_estimates")
    op.drop_table("conversion_factor_estimates")
