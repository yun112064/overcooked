"""G07 knowledge crawl requests: claimed status for the team-computer internal API

團隊電腦改經雲端內部 API 認領／完成補抓請求（不再直接連雲端資料庫）：新增 claimed 狀態與 claimed_at。

Revision ID: f5c1a8d3e962
Revises: e2b7c4a9f310
Create Date: 2026-10-21 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f5c1a8d3e962"
down_revision: Union[str, Sequence[str], None] = "e2b7c4a9f310"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("knowledge_crawl_requests", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_knowledge_crawl_request_status", "knowledge_crawl_requests", type_="check")
    op.create_check_constraint("ck_knowledge_crawl_request_status", "knowledge_crawl_requests",
                               "status IN ('pending', 'claimed', 'crawled')")


def downgrade() -> None:
    op.execute("UPDATE knowledge_crawl_requests SET status = 'pending' WHERE status = 'claimed'")
    op.drop_constraint("ck_knowledge_crawl_request_status", "knowledge_crawl_requests", type_="check")
    op.create_check_constraint("ck_knowledge_crawl_request_status", "knowledge_crawl_requests",
                               "status IN ('pending', 'crawled')")
    op.drop_column("knowledge_crawl_requests", "claimed_at")
