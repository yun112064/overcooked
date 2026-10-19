"""merge T01 recipes, T02 pricing and T07–T10 branches

三條遷移線都接在 c4afb1c5751e（pgvector）之後，彼此沒有共用資料表，
此 revision 只合併 head，不做任何結構變更。

Revision ID: e6a1f3c8d905
Revises: d4e8b6a03c71, 9c7e2a1f4b60, a92c6d03e71f
Create Date: 2026-10-19 10:00:00
"""
from typing import Sequence, Union

revision: str = "e6a1f3c8d905"
down_revision: Union[str, Sequence[str], None] = ("d4e8b6a03c71", "9c7e2a1f4b60", "a92c6d03e71f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
