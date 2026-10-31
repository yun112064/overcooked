"""Version recipe data loaded from an immutable SQL seed.

Revision ID: 1f6a2d9c4b70
Revises: 0d52b9a6c831
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1f6a2d9c4b70"
down_revision = "0d52b9a6c831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_data_releases",
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("dataset_version", sa.String(64), nullable=False, unique=True),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column(
            "row_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('legacy', 'sql_seed')",
            name="ck_recipe_data_releases_source_kind",
        ),
    )
    op.create_index(
        "uq_recipe_data_releases_active",
        "recipe_data_releases",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.execute(
        """
        INSERT INTO recipe_data_releases (
            release_id, dataset_version, source_sha256, schema_version,
            source_kind, source_uri, row_counts, active
        ) VALUES (
            'legacy-1017', 'recipe-1017-legacy',
            '6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304',
            't01-recipe-import-v1', 'legacy', NULL, '{}'::jsonb, false
        )
        """
    )

    for table_name in (
        "source_archive",
        "ingredient_aliases",
        "allergens",
        "recipes",
        "recipe_steps",
        "recipe_ingredients",
    ):
        op.add_column(
            table_name,
            sa.Column(
                "recipe_release_id",
                sa.String(64),
                nullable=False,
                server_default="legacy-1017",
            ),
        )
        op.create_foreign_key(
            f"fk_{table_name}_recipe_release",
            table_name,
            "recipe_data_releases",
            ["recipe_release_id"],
            ["release_id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table_name}_recipe_release_id",
            table_name,
            ["recipe_release_id"],
        )


def downgrade() -> None:
    for table_name in (
        "recipe_ingredients",
        "recipe_steps",
        "recipes",
        "allergens",
        "ingredient_aliases",
        "source_archive",
    ):
        op.drop_index(f"ix_{table_name}_recipe_release_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_recipe_release", table_name, type_="foreignkey"
        )
        op.drop_column(table_name, "recipe_release_id")
    op.drop_index("uq_recipe_data_releases_active", table_name="recipe_data_releases")
    op.drop_table("recipe_data_releases")
