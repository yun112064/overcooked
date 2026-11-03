"""T01 Excel releases, explicit serving basis and pre-cooking weight evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d1103a01b001"
down_revision = "c1e7a4b9d260"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_recipe_data_releases_source_kind", "recipe_data_releases", type_="check")
    op.create_check_constraint("ck_recipe_data_releases_source_kind", "recipe_data_releases",
                               "source_kind IN ('legacy', 'sql_seed', 'xlsx_import')")
    op.add_column("recipes", sa.Column("servings_basis", sa.String(24), nullable=False, server_default="portions"))
    op.add_column("recipe_ingredients", sa.Column("weight_basis", sa.String(32), nullable=True))
    op.add_column("recipe_ingredients", sa.Column("weight_evidence", postgresql.JSONB(astext_type=sa.Text()),
                                                  nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    # PostgreSQL refuses restoration of the old CHECK while xlsx releases remain.
    # Never silently delete or relabel imported releases during a downgrade.
    op.drop_constraint("ck_recipe_data_releases_source_kind", "recipe_data_releases", type_="check")
    op.create_check_constraint("ck_recipe_data_releases_source_kind", "recipe_data_releases",
                               "source_kind IN ('legacy', 'sql_seed')")
    op.drop_column("recipe_ingredients", "weight_evidence")
    op.drop_column("recipe_ingredients", "weight_basis")
    op.drop_column("recipes", "servings_basis")
