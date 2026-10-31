"""G07 knowledge crawl-request queue: final ingredients that had no approved knowledge

生成結果的最終食材若沒有審核通過的知識，雲端記錄其 FOOD 編號；團隊電腦的健康醫療網爬蟲優先處理。
只存 FOOD 編號、次數與時間，不存家庭、使用者或問卷資料。

Revision ID: e2b7c4a9f310
Revises: d9a4b7e2c5f1
Create Date: 2026-10-20 23:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e2b7c4a9f310"
down_revision: Union[str, Sequence[str], None] = "d9a4b7e2c5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_crawl_requests",
        sa.Column("food_id", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'crawled')", name="ck_knowledge_crawl_request_status"),
        sa.CheckConstraint("food_id ~ '^FOOD[0-9]{6}$'", name="ck_knowledge_crawl_request_food_id"),
    )
    op.create_index("ix_knowledge_crawl_requests_pending", "knowledge_crawl_requests",
                    ["status", "request_count"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_crawl_requests_pending", table_name="knowledge_crawl_requests")
    op.drop_table("knowledge_crawl_requests")
