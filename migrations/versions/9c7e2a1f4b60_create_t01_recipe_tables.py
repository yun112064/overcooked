"""create T01 six-table recipe schema

Revision ID: 9c7e2a1f4b60
Revises: c4afb1c5751e
Create Date: 2026-10-18

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c7e2a1f4b60"
down_revision: Union[str, Sequence[str], None] = "c4afb1c5751e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_archive",
        sa.Column("raw_id", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_recipe_id", sa.String(length=200), nullable=False),
        sa.Column("recipe_name_raw", sa.Text(), nullable=False),
        sa.Column("servings_raw", sa.String(length=100), nullable=False),
        sa.Column("ingredients_raw", sa.Text(), nullable=False),
        sa.Column("steps_raw", sa.Text(), nullable=False),
        sa.Column("tags_raw", sa.Text(), nullable=False),
        sa.Column("license_name", sa.String(length=200), nullable=True),
        sa.Column("rights_uri", sa.Text(), nullable=True),
        sa.Column("rights_status", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_version", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("import_batch_id", sa.String(length=64), nullable=False),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("quality_issues", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.PrimaryKeyConstraint("raw_id"),
        sa.UniqueConstraint(
            "source_name", "source_recipe_id", name="uq_source_archive_external_recipe"
        ),
    )
    op.create_index("ix_source_archive_import_batch_id", "source_archive", ["import_batch_id"])

    op.create_table(
        "ingredient_aliases",
        sa.Column("food_id", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("ambiguous_aliases", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("quality_issues", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.PrimaryKeyConstraint("food_id"),
    )

    op.create_table(
        "allergens",
        sa.Column("allergen_id", sa.String(length=32), nullable=False),
        sa.Column("allergen_name", sa.String(length=200), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=True),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.PrimaryKeyConstraint("allergen_id"),
        sa.UniqueConstraint("allergen_name"),
    )

    op.create_table(
        "recipes",
        sa.Column("recipe_id", sa.String(length=32), nullable=False),
        sa.Column("raw_id", sa.String(length=32), nullable=False),
        sa.Column("standard_dish_id", sa.String(length=32), nullable=False),
        sa.Column("recipe_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("name_aliases", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("servings_raw", sa.String(length=100), nullable=False),
        sa.Column("original_servings", sa.Numeric(10, 3), nullable=True),
        sa.Column("servings_status", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("meal_type", sa.String(length=100), nullable=False),
        sa.Column("cuisine", sa.String(length=100), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("allergens", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("cooking_methods", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("cooking_method_status", sa.String(length=32), server_default="unreviewed", nullable=False),
        sa.Column("duplicate_status", sa.String(length=32), server_default="unique_candidate", nullable=False),
        sa.Column("duplicate_of_recipe_id", sa.String(length=32), nullable=True),
        sa.Column("duplicate_candidate_recipe_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("quality_status", sa.String(length=32), server_default="not_qualified", nullable=False),
        sa.Column("is_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("quality_issues", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.CheckConstraint(
            "original_servings IS NULL OR original_servings > 0",
            name="ck_recipe_servings_positive",
        ),
        sa.ForeignKeyConstraint(["duplicate_of_recipe_id"], ["recipes.recipe_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["raw_id"], ["source_archive.raw_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("recipe_id"),
        sa.UniqueConstraint("raw_id"),
    )
    op.create_index("ix_recipes_eligible", "recipes", ["is_eligible"])
    op.create_index("ix_recipes_normalized_name", "recipes", ["normalized_name"])
    op.create_index("ix_recipes_standard_dish_id", "recipes", ["standard_dish_id"])

    op.create_table(
        "recipe_steps",
        sa.Column("step_id", sa.String(length=32), nullable=False),
        sa.Column("recipe_id", sa.String(length=32), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("instruction_clean", sa.Text(), nullable=False),
        sa.Column("cooking_method", sa.String(length=100), nullable=True),
        sa.Column("method_status", sa.String(length=32), server_default="unreviewed", nullable=False),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.CheckConstraint("step_no > 0", name="ck_recipe_step_positive"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.recipe_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("step_id"),
        sa.UniqueConstraint("recipe_id", "step_no", name="uq_recipe_step_number"),
    )
    op.create_index("ix_recipe_steps_recipe_id", "recipe_steps", ["recipe_id"])

    op.create_table(
        "recipe_ingredients",
        sa.Column("recipe_ingredient_id", sa.String(length=32), nullable=False),
        sa.Column("recipe_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Numeric(10, 3), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("food_id", sa.String(length=32), nullable=False),
        sa.Column("source_amount", sa.Numeric(16, 6), nullable=True),
        sa.Column("source_unit", sa.String(length=100), nullable=True),
        sa.Column("source_weight_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("amount", sa.Numeric(16, 6), nullable=True),
        sa.Column("amount_min", sa.Numeric(16, 6), nullable=True),
        sa.Column("amount_max", sa.Numeric(16, 6), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("quantity_status", sa.String(length=32), nullable=False),
        sa.Column("unit_status", sa.String(length=32), nullable=False),
        sa.Column("weight_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("weight_status", sa.String(length=32), nullable=False),
        sa.Column("adjustable_min", sa.Numeric(16, 6), nullable=True),
        sa.Column("adjustable_max", sa.Numeric(16, 6), nullable=True),
        sa.Column("adjustment_status", sa.String(length=32), server_default="not_reviewed", nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=True),
        sa.Column("source_protein_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("source_carbohydrates_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("source_fat_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("source_sugar_g", sa.Numeric(16, 6), nullable=True),
        sa.Column("source_sodium_mg", sa.Numeric(16, 6), nullable=True),
        sa.Column("allergens", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("quality_issues", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_recipe_ingredient_sequence_positive"),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_recipe_ingredient_amount_nonnegative"),
        sa.CheckConstraint("amount_min IS NULL OR amount_min >= 0", name="ck_recipe_ingredient_amount_min_nonnegative"),
        sa.CheckConstraint("amount_max IS NULL OR amount_max >= 0", name="ck_recipe_ingredient_amount_max_nonnegative"),
        sa.CheckConstraint(
            "amount_min IS NULL OR amount_max IS NULL OR amount_min <= amount_max",
            name="ck_recipe_ingredient_amount_range",
        ),
        sa.CheckConstraint("weight_g IS NULL OR weight_g >= 0", name="ck_recipe_ingredient_weight_nonnegative"),
        sa.CheckConstraint(
            "adjustable_min IS NULL OR adjustable_max IS NULL OR adjustable_min <= adjustable_max",
            name="ck_recipe_ingredient_adjustable_range",
        ),
        sa.ForeignKeyConstraint(["food_id"], ["ingredient_aliases.food_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.recipe_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recipe_ingredient_id"),
        sa.UniqueConstraint("recipe_id", "sequence", name="uq_recipe_ingredient_sequence"),
    )
    op.create_index("ix_recipe_ingredients_food_id", "recipe_ingredients", ["food_id"])
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"])


def downgrade() -> None:
    op.drop_index("ix_recipe_ingredients_recipe_id", table_name="recipe_ingredients")
    op.drop_index("ix_recipe_ingredients_food_id", table_name="recipe_ingredients")
    op.drop_table("recipe_ingredients")
    op.drop_index("ix_recipe_steps_recipe_id", table_name="recipe_steps")
    op.drop_table("recipe_steps")
    op.drop_index("ix_recipes_standard_dish_id", table_name="recipes")
    op.drop_index("ix_recipes_normalized_name", table_name="recipes")
    op.drop_index("ix_recipes_eligible", table_name="recipes")
    op.drop_table("recipes")
    op.drop_table("allergens")
    op.drop_table("ingredient_aliases")
    op.drop_index("ix_source_archive_import_batch_id", table_name="source_archive")
    op.drop_table("source_archive")

