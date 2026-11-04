"""T15 result retry jobs: job parameters and derived results produced by a job

「只重試知識說明」（pre-review #3）與缺價定向查價（P3）改由背景工作產生新結果版本：
- background_jobs.params：工作參數（例如來源結果 ID、要查價的 FOOD 編號），不含個人資料；
- generation_results 的來源規則改為「工作或來源版本至少一個」：重試工作產生的版本兩者都有。

Revision ID: a3d6e9b1c4f7
Revises: f5c1a8d3e962
Create Date: 2026-10-21 11:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a3d6e9b1c4f7"
down_revision: Union[str, Sequence[str], None] = "f5c1a8d3e962"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("params", postgresql.JSONB(), nullable=True))
    op.drop_constraint("ck_generation_result_origin", "generation_results", type_="check")
    op.create_check_constraint("ck_generation_result_origin", "generation_results",
                               "job_id IS NOT NULL OR derived_from_result_id IS NOT NULL")


def downgrade() -> None:
    # 舊規則二擇一：重試工作產生的版本只保留來源版本連結
    op.execute("UPDATE generation_results SET job_id = NULL "
               "WHERE job_id IS NOT NULL AND derived_from_result_id IS NOT NULL")
    op.drop_constraint("ck_generation_result_origin", "generation_results", type_="check")
    op.create_check_constraint("ck_generation_result_origin", "generation_results",
                               "(job_id IS NULL) <> (derived_from_result_id IS NULL)")
    op.drop_column("background_jobs", "params")
