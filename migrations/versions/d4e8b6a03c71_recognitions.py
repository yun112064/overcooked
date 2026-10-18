"""T09 vegetable recognitions

Revision ID: d4e8b6a03c71
Revises: c2d9a7f15e83
Create Date: 2026-10-18 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d4e8b6a03c71"
down_revision: Union[str, Sequence[str], None] = "c2d9a7f15e83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recognitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("image_path", sa.String(200), nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("top_label", sa.String(100), nullable=True),
        sa.Column("top_name", sa.String(100), nullable=True),
        sa.Column("top_confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("detections", postgresql.JSONB(), nullable=True),
        sa.Column("unavailable_reason", sa.String(50), nullable=True),
        sa.Column("confirmed_name", sa.String(100), nullable=True),
        sa.Column("confirmed_ingredient_id", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'no_result', 'unavailable')", name="ck_recognition_status"),
    )
    op.create_index("ix_recognitions_household_id", "recognitions", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_recognitions_household_id", table_name="recognitions")
    op.drop_table("recognitions")
