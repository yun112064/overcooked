"""decisions #32 TFDA required-nutrient gap estimates (kind=nutrient_fill)

沿用 conversion_factor_estimates：新增 kind=nutrient_fill，food_id 為 TFDA 樣本 ID、factor_key 為營養素
（energy_kcal／protein_g），新欄位 nutrient_per_100g 為每 100 g 推估值。只補 TFDA 空白欄位；人工覆寫優先，
rejected 永不使用。

Revision ID: c6a9d2e4f817
Revises: b7f2c9e4a8d1
Create Date: 2026-10-21 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c6a9d2e4f817"
down_revision: Union[str, Sequence[str], None] = "b7f2c9e4a8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "conversion_factor_estimates"
OLD_VALUES = ("(kind = 'cooking' AND grams_per_unit IS NULL AND energy_retention IS NOT NULL "
              "AND protein_retention IS NOT NULL) OR "
              "(kind = 'unit' AND grams_per_unit IS NOT NULL AND yield_factor IS NULL "
              "AND energy_retention IS NULL AND protein_retention IS NULL) OR "
              "(kind = 'tfda_mapping' AND grams_per_unit IS NULL AND yield_factor IS NULL "
              "AND energy_retention IS NULL AND protein_retention IS NULL)")
NEW_VALUES = (OLD_VALUES + " OR (kind = 'nutrient_fill' AND nutrient_per_100g IS NOT NULL AND grams_per_unit IS NULL "
              "AND yield_factor IS NULL AND energy_retention IS NULL AND protein_retention IS NULL)")
OLD_KINDS = "kind IN ('cooking', 'unit', 'tfda_mapping')"
NEW_KINDS = "kind IN ('cooking', 'unit', 'tfda_mapping', 'nutrient_fill')"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("nutrient_per_100g", sa.Numeric(10, 4), nullable=True))
    op.drop_constraint("ck_conversion_factor_kind", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_kind", TABLE, NEW_KINDS)
    op.drop_constraint("ck_conversion_factor_values", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_values", TABLE, NEW_VALUES)
    op.create_check_constraint("ck_conversion_factor_nutrient_fill", TABLE,
                               "nutrient_per_100g IS NULL OR (kind = 'nutrient_fill' AND nutrient_per_100g >= 0 "
                               "AND nutrient_per_100g <= 900)")


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE kind = 'nutrient_fill'")
    op.drop_constraint("ck_conversion_factor_nutrient_fill", TABLE, type_="check")
    op.drop_constraint("ck_conversion_factor_values", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_values", TABLE, OLD_VALUES)
    op.drop_constraint("ck_conversion_factor_kind", TABLE, type_="check")
    op.create_check_constraint("ck_conversion_factor_kind", TABLE, OLD_KINDS)
    op.drop_column(TABLE, "nutrient_per_100g")
