"""Bind nutrition-only source releases to the current recipe publication.

Revision ID: d1103c04a001
Revises: d1103b02a001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd1103c04a001'
down_revision = 'd1103b02a001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'nutrition_data_releases',
        sa.Column('release_id', sa.String(64), primary_key=True),
        sa.Column('import_id', sa.String(64), sa.ForeignKey('nutrition_imports.import_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('recipe_release_id', sa.String(64), sa.ForeignKey('recipe_data_releases.release_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('recipe_source_sha256', sa.String(64), nullable=False),
        sa.Column('mapping_version', sa.String(100), nullable=False, unique=True),
        sa.Column('manifest_sha256', sa.String(64), nullable=False),
        sa.Column('source_manifest', postgresql.JSONB(), nullable=False),
        sa.Column('row_counts', postgresql.JSONB(), nullable=False),
        sa.Column('usage_status', sa.String(40), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("usage_status = 'observations_only_pending_review'", name='ck_nutrition_release_usage'),
    )
    op.create_index('uq_nutrition_release_active', 'nutrition_data_releases', ['recipe_release_id'],
                    unique=True, postgresql_where=sa.text('active'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM nutrition_data_releases LIMIT 1')).first():
        raise RuntimeError('Nutrition releases exist; refusing to remove source lineage')
    op.drop_index('uq_nutrition_release_active', table_name='nutrition_data_releases')
    op.drop_table('nutrition_data_releases')
