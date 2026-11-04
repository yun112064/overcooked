"""Store approved recipe covers in PostgreSQL."""
import sqlalchemy as sa
from alembic import op

revision = "d1104covers1"
down_revision = "d1103d04b001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_recipes_id_release", "recipes", ["recipe_id", "recipe_release_id"])
    op.create_table(
        "recipe_covers",
        sa.Column("recipe_id", sa.String(32), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["recipe_id", "recipe_release_id"], ["recipes.recipe_id", "recipes.recipe_release_id"],
            name="fk_recipe_covers_recipe_release", ondelete="CASCADE",
        ),
        sa.CheckConstraint("media_type IN ('image/jpeg', 'image/png', 'image/webp')", name="ck_recipe_covers_media_type"),
    )


def downgrade():
    op.drop_table("recipe_covers")
    op.drop_constraint("uq_recipes_id_release", "recipes", type_="unique")
