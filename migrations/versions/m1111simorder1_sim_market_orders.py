"""Persist isolated simulated checkout receipts."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = 'm1111simorder1'
down_revision = 'm1109mealflow1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sim_market_orders',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('plan_id', sa.Uuid(), sa.ForeignKey('adopted_meal_plans.id'), nullable=False),
        sa.Column('handoff_id', sa.String(22), nullable=False, unique=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('receipt', JSONB(), nullable=False))
    op.create_index('ix_sim_market_orders_plan_id', 'sim_market_orders', ['plan_id'])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM sim_market_orders)')):
        raise RuntimeError('Back up simulated order receipts before removing this table.')
    op.drop_table('sim_market_orders')
