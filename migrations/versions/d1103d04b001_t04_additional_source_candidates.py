"""Additional T04 source evidence without approval or nutrient overwrites."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='d1103d04b001'
down_revision='d1103c04a001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('nutrition_product_sources',
        sa.Column('product_source_id',sa.String(64),primary_key=True),
        sa.Column('recipe_release_id',sa.String(64),sa.ForeignKey('recipe_data_releases.release_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('food_id',sa.String(32),sa.ForeignKey('ingredient_aliases.food_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_name',sa.String(200),nullable=False),sa.Column('source_url',sa.Text(),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),sa.Column('per_100g',JSONB(),nullable=False),
        sa.Column('provenance',JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP')))
    op.create_table('nutrition_source_candidates',
        sa.Column('candidate_id',sa.String(64),primary_key=True),
        sa.Column('recipe_release_id',sa.String(64),sa.ForeignKey('recipe_data_releases.release_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('nutrition_import_id',sa.String(64),sa.ForeignKey('nutrition_imports.import_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('food_id',sa.String(32),sa.ForeignKey('ingredient_aliases.food_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_id',sa.String(64),sa.ForeignKey('nutrition_sources.source_id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_fingerprint',sa.String(64),nullable=False),
        sa.Column('policy_version',sa.String(64),nullable=False),
        sa.Column('match_evidence',JSONB(),nullable=False),
        sa.Column('review_status',sa.String(24),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('recipe_release_id','nutrition_import_id','food_id','source_id','policy_version',name='uq_nutrition_source_candidate'),
        sa.CheckConstraint("review_status = 'pending_review'",name='ck_nutrition_candidate_review'))


def downgrade():
    if (op.get_bind().execute(sa.text('SELECT 1 FROM nutrition_source_candidates LIMIT 1')).first()
            or op.get_bind().execute(sa.text('SELECT 1 FROM nutrition_product_sources LIMIT 1')).first()):
        raise RuntimeError('Source evidence exists; refusing to delete candidate lineage')
    op.drop_table('nutrition_source_candidates')
    op.drop_table('nutrition_product_sources')
