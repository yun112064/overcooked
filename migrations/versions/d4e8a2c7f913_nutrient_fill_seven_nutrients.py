"""decisions #45 nutrient_fill covers all seven nutrients (sodium up to 40000 mg per 100 g)

kind=nutrient_fill 原本只補熱量／蛋白質（上限 900）；擴充到碳水、脂肪、糖、膳食纖維、鈉後，
鈉（mg／100 g）可達約 40000（純鹽），放寬 nutrient_per_100g 的上限。各營養素的細部範圍由
app.estimation.store.check_fill 檢查。

Revision ID: d4e8a2c7f913
Revises: c6a9d2e4f817
Create Date: 2026-10-21 23:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4e8a2c7f913"
down_revision: Union[str, Sequence[str], None] = "c6a9d2e4f817"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "conversion_factor_estimates"
NAME = "ck_conversion_factor_nutrient_fill"


def _check(upper: int) -> str:
    return (f"nutrient_per_100g IS NULL OR (kind = 'nutrient_fill' AND nutrient_per_100g >= 0 "
            f"AND nutrient_per_100g <= {upper})")


def upgrade() -> None:
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, _check(40000))


def downgrade() -> None:
    # 舊上限 900 容不下鈉等新營養素的推估：降版時移除必要營養以外的 nutrient_fill 列
    op.execute(f"DELETE FROM {TABLE} WHERE kind = 'nutrient_fill' "
               "AND factor_key NOT IN ('energy_kcal', 'protein_g')")
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, _check(900))
