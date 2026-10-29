"""T09 recognitions: one photo can confirm several vegetables

confirmed_name / confirmed_ingredient_id 改為 confirmed_items JSONB 清單，既有確認搬入清單。

Revision ID: f3b7c1d9e204
Revises: e6a1f3c8d905
Create Date: 2026-10-19 11:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f3b7c1d9e204"
down_revision: Union[str, Sequence[str], None] = "e6a1f3c8d905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recognitions", sa.Column(
        "confirmed_items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.execute("""
        UPDATE recognitions
        SET confirmed_items = jsonb_build_array(
            jsonb_build_object('name', confirmed_name, 'ingredient_id', confirmed_ingredient_id))
        WHERE confirmed_ingredient_id IS NOT NULL
    """)
    op.drop_column("recognitions", "confirmed_ingredient_id")
    op.drop_column("recognitions", "confirmed_name")


def downgrade() -> None:
    op.add_column("recognitions", sa.Column("confirmed_name", sa.String(100), nullable=True))
    op.add_column("recognitions", sa.Column("confirmed_ingredient_id", sa.String(20), nullable=True))
    # 只能保留最後一筆確認
    op.execute("""
        UPDATE recognitions
        SET confirmed_name = confirmed_items->-1->>'name',
            confirmed_ingredient_id = confirmed_items->-1->>'ingredient_id'
        WHERE jsonb_array_length(confirmed_items) > 0
    """)
    op.drop_column("recognitions", "confirmed_items")
