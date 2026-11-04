"""Bind T02 snapshots to recipe releases and retain import provenance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d1103b02a001"
down_revision = "d1103a01b001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_data_releases",
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64), sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_ref", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("row_counts", postgresql.JSONB(), nullable=False),
        sa.Column("usage_status", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("usage_status IN ('historical_requires_refresh', 'live_verified')", name="ck_price_release_usage"),
    )
    op.create_index("uq_price_release_active_recipe", "price_data_releases", ["recipe_release_id"],
                    unique=True, postgresql_where=sa.text("active"))
    op.add_column("price_collection_runs", sa.Column("recipe_release_id", sa.String(64),
        sa.ForeignKey("recipe_data_releases.release_id", name="fk_price_run_recipe_release", ondelete="RESTRICT")))
    op.add_column("price_collection_runs", sa.Column("price_data_release_id", sa.String(64),
        sa.ForeignKey("price_data_releases.release_id", name="fk_price_run_data_release", ondelete="RESTRICT")))
    op.create_table(
        "price_snapshot_foods",
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("price_quote_snapshots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64), sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("mapping_method", sa.String(40), nullable=False),
    )
    op.create_index("ix_price_snapshot_food_scope", "price_snapshot_foods", ["recipe_release_id", "food_id", "snapshot_id"])
    op.create_index("ix_price_runs_release", "price_collection_runs", ["recipe_release_id", "price_data_release_id"])


def downgrade():
    # A downgrade must not silently discard imported source provenance.
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM price_data_releases")).scalar_one()
    if count:
        raise RuntimeError("Price releases exist; refusing to discard provenance during downgrade")
    op.drop_index("ix_price_runs_release", table_name="price_collection_runs")
    op.drop_table("price_snapshot_foods")
    op.drop_column("price_collection_runs", "price_data_release_id")
    op.drop_column("price_collection_runs", "recipe_release_id")
    op.drop_table("price_data_releases")
