"""Merge the SQL recipe/T11-T12 history with the T13-T20 feature history.

Revision ID: f1a8c4d2e709
Revises: d2f7a8c91e04, d4e8a2c7f913
"""

from collections.abc import Sequence


revision: str = "f1a8c4d2e709"
down_revision: tuple[str, str] = ("d2f7a8c91e04", "d4e8a2c7f913")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
