"""Record operational milestones alongside committed meal confirmations."""
import sqlalchemy as sa
from alembic import op

revision = "m1109mealflow1"
down_revision = "m1107management1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meal_confirmation_events",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("adopted_meal_plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meal_confirmation_event_recent", "meal_confirmation_events", ["stage", "occurred_at", "plan_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM meal_confirmation_events)")):
        raise RuntimeError("Meal confirmation events exist; back up the event journal before removing it.")
    op.drop_table("meal_confirmation_events")
