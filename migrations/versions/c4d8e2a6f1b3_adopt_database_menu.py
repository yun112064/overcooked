"""Adopt or reject the database menu of a questionnaire (2026-11-03, modeled on t11-t20-demo T16).

Extends T11 adopted_meal_plans in place (no parallel table):
- meal_request_id / request_version: the questionnaire version that was adopted; SET NULL when the
  questionnaire is deleted so the history and snapshot stay. One adoption per questionnaire.
- idempotency_key: (household_id, idempotency_key) unique, so a resent click adopts once.
- snapshot: dishes with amounts and steps, staple, seven nutrients, per-person portions and targets.
- adopted_by: the user who adopted.
T11 minimal records keep these columns NULL / default.

meal_menu_rejections: menus the user chose not to adopt (「不採用，換一份」); the next query skips
those recipe combinations for the same questionnaire version.

Revision ID: c4d8e2a6f1b3
Revises: b72e9c014af6
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4d8e2a6f1b3"
down_revision = "b72e9c014af6"
branch_labels = None
depends_on = None

TABLE = "adopted_meal_plans"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                                   sa.ForeignKey("meal_requests.id", ondelete="SET NULL",
                                                 name="fk_adopted_meal_plans_meal_request"), nullable=True))
    op.add_column(TABLE, sa.Column("request_version", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column(TABLE, sa.Column("snapshot", postgresql.JSONB(), nullable=False,
                                   server_default=sa.text("'{}'::jsonb")))
    op.add_column(TABLE, sa.Column("adopted_by", postgresql.UUID(as_uuid=True),
                                   sa.ForeignKey("users.id", ondelete="SET NULL",
                                                 name="fk_adopted_meal_plans_adopted_by"), nullable=True))
    op.create_unique_constraint("uq_adopted_meal_plan_idempotency", TABLE, ["household_id", "idempotency_key"])
    op.create_unique_constraint("uq_adopted_meal_plan_request", TABLE, ["meal_request_id"])
    op.create_table(
        "meal_menu_rejections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("recipe_ids", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_meal_menu_rejections_request", "meal_menu_rejections", ["meal_request_id", "request_version"])


def downgrade() -> None:
    op.drop_index("ix_meal_menu_rejections_request", table_name="meal_menu_rejections")
    op.drop_table("meal_menu_rejections")
    op.drop_constraint("uq_adopted_meal_plan_request", TABLE, type_="unique")
    op.drop_constraint("uq_adopted_meal_plan_idempotency", TABLE, type_="unique")
    for column in ("adopted_by", "snapshot", "idempotency_key", "request_version", "meal_request_id"):
        op.drop_column(TABLE, column)
