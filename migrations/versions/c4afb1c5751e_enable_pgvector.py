"""enable pgvector

Revision ID: c4afb1c5751e
Revises: f16133cc3730
Create Date: 2026-10-11 22:55:41.969034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4afb1c5751e'
down_revision: Union[str, Sequence[str], None] = 'f16133cc3730'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")