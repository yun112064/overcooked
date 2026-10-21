"""decisions #28 recipe ingredient -> TFDA sample mapping cache

沿用 conversion_factor_estimates：新增 kind=tfda_mapping（key 固定為 "tfda"），值存在新欄位 tfda_sample_id
（NULL 代表模型回答「無合適樣本」，只用來讓批次冪等）。人工核可／覆寫優先，rejected 永不使用。

Revision ID: b7f2c9e4a8d1
Revises: e8b2d5f7a1c3
Create Date: 2026-10-21 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7f2c9e4a8d1"
down_revision: Union[str, Sequence[str], None] = "e8b2d5f7a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "conversion_factor_estimates"
OLD_VALUES = ("(kind = 'cooking' AND grams_per_unit IS NULL AND energy_retention IS NOT NULL "
              "AND protein_retention IS NOT NULL) OR "
              "(kind = 'unit' AND grams_per_unit IS NOT NULL AND yield_factor IS NULL "
              "AND energy_retention IS NULL AND protein_retention IS NULL)")
NEW_VALUES = (OLD_VALUES + " OR (kind = 'tfda_mapping' AND grams_per_unit IS NULL AND yield_factor IS NULL "
              "AND energy_retention IS NULL AND protein_retention IS NULL)")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("tfda_sample_id", sa.String(32), nullable=True))
    op.drop_constraint("ck_conversion_factor_kind", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_kind", TABLE, "kind IN ('cooking', 'unit', 'tfda_mapping')")
    op.drop_constraint("ck_conversion_factor_values", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_values", TABLE, NEW_VALUES)
    op.create_check_constraint("ck_conversion_factor_tfda_sample", TABLE,
                               "tfda_sample_id IS NULL OR kind = 'tfda_mapping'")


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE kind = 'tfda_mapping'")
    op.drop_constraint("ck_conversion_factor_tfda_sample", TABLE, type_="check")
    op.drop_constraint("ck_conversion_factor_values", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_values", TABLE, OLD_VALUES)
    op.drop_constraint("ck_conversion_factor_kind", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_kind", TABLE, "kind IN ('cooking', 'unit')")
    op.drop_column(TABLE, "tfda_sample_id")
