"""T07 identity, households and members

Revision ID: a7e3c2d91b04
Revises: c4afb1c5751e
Create Date: 2026-10-18 16:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7e3c2d91b04"
down_revision: Union[str, Sequence[str], None] = "c4afb1c5751e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "households",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "household_memberships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "household_id"),
    )
    op.create_index("ix_household_memberships_user_id", "household_memberships", ["user_id"])
    op.create_index("ix_household_memberships_household_id", "household_memberships", ["household_id"])

    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    op.create_table(
        "household_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("household_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(50), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("equation_sex", sa.String(1), nullable=False),
        sa.Column("weight_kg", sa.Numeric(6, 2), nullable=False),
        sa.Column("activity_category", sa.String(20), nullable=False),
        sa.Column("goal", sa.String(20), nullable=False),
        sa.Column("portion", sa.Numeric(3, 2), nullable=False),
        sa.Column("allergen_ingredient_ids", postgresql.JSONB(), nullable=False),
        sa.Column("avoided_ingredient_ids", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("portion IN (0.70, 0.85, 1.00, 1.15, 1.30)", name="ck_member_portion"),
        sa.CheckConstraint("equation_sex IN ('M', 'F')", name="ck_member_equation_sex"),
        sa.CheckConstraint(
            "activity_category IN ('sedentary', 'low_active', 'active', 'highly_active')",
            name="ck_member_activity",
        ),
        sa.CheckConstraint(
            "goal IN ('general', 'maintain', 'muscle_gain', 'fat_loss')", name="ck_member_goal"
        ),
        sa.CheckConstraint("weight_kg > 0", name="ck_member_weight"),
    )
    op.create_index("ix_household_members_household_id", "household_members", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_household_members_household_id", table_name="household_members")
    op.drop_table("household_members")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_household_memberships_household_id", table_name="household_memberships")
    op.drop_index("ix_household_memberships_user_id", table_name="household_memberships")
    op.drop_table("household_memberships")
    op.drop_table("households")
    op.drop_table("users")
