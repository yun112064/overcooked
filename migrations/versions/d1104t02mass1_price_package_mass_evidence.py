"""Store verified net mass for the exact retailer SKU and package."""

import sqlalchemy as sa
from alembic import op


revision = "d1104t02mass1"
down_revision = "d1104covers1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_package_mass_evidence",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("product_id", sa.Text(), primary_key=True),
        sa.Column("volume_ml", sa.Numeric(14, 4), primary_key=True),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("net_mass_g", sa.Numeric(14, 4), nullable=False),
        sa.Column("source_snapshot_id", sa.BigInteger(),
                  sa.ForeignKey("price_quote_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_detail", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("volume_ml > 0 AND net_mass_g > 0", name="ck_price_package_mass_positive"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM price_package_mass_evidence")):
        raise RuntimeError("Verified package masses exist; refusing to discard their provenance")
    op.drop_table("price_package_mass_evidence")
