"""create recipe tables

Revision ID: 3d4a4d3b0e61
Revises: c4afb1c5751e
Create Date: 2026-10-17 09:51:47.452044
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3d4a4d3b0e61'
down_revision: Union[str, Sequence[str], None] = 'c4afb1c5751e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recipe_source_archive',
        sa.Column('raw_id', sa.String(32), primary_key=True),
        sa.Column('source_name', sa.Text()),
        sa.Column('source_url', sa.Text()),
        sa.Column('source_recipe_id', sa.String(128)),
        sa.Column('recipe_name_raw', sa.Text()),
        sa.Column('servings_raw', sa.Text()),
        sa.Column('ingredients_raw', sa.Text()),
        sa.Column('steps_raw', sa.Text()),
        sa.Column('tags_raw', sa.Text()),
    )

    op.create_table(
        'recipes',
        sa.Column('recipe_id', sa.String(32), primary_key=True),
        sa.Column('raw_id', sa.String(32), sa.ForeignKey('recipe_source_archive.raw_id')),
        sa.Column('recipe_name', sa.Text(), nullable=False),
        sa.Column('servings_raw', sa.Text()),
        sa.Column('category', sa.Text()),
        sa.Column('meal_type', sa.Text()),
        sa.Column('cuisine', sa.Text()),
        sa.Column('source_name', sa.Text()),
        sa.Column('source_url', sa.Text()),
        sa.Column('allergens', sa.Text()),
    )
    op.create_index('ix_recipes_recipe_name', 'recipes', ['recipe_name'])
    op.create_index('ix_recipes_category_meal_type', 'recipes', ['category', 'meal_type'])

    op.create_table(
        'recipe_ingredients',
        sa.Column('recipe_ingredient_id', sa.String(32), primary_key=True),
        sa.Column('recipe_id', sa.String(32), sa.ForeignKey('recipes.recipe_id', ondelete='CASCADE'), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('raw_text', sa.Text()),
        sa.Column('canonical_name', sa.Text()),
        sa.Column('ingredient_id', sa.String(32)),
        sa.Column('amount', sa.Numeric(18, 6)),
        sa.Column('unit', sa.Text()),
        sa.Column('weight_g', sa.Numeric(18, 6)),
        sa.Column('protein_g', sa.Numeric(18, 6)),
        sa.Column('carbohydrates_g', sa.Numeric(18, 6)),
        sa.Column('fat_g', sa.Numeric(18, 6)),
        sa.Column('sugar_g', sa.Numeric(18, 6)),
        sa.Column('sodium_mg', sa.Numeric(18, 6)),
        sa.Column('allergens', sa.Text()),
        sa.UniqueConstraint('recipe_id', 'sequence', name='uq_recipe_ingredients_order'),
    )
    op.create_index('ix_recipe_ingredients_recipe_id', 'recipe_ingredients', ['recipe_id'])
    op.create_index('ix_recipe_ingredients_ingredient_id', 'recipe_ingredients', ['ingredient_id'])

    op.create_table(
        'recipe_steps',
        sa.Column('step_id', sa.String(32), primary_key=True),
        sa.Column('recipe_id', sa.String(32), sa.ForeignKey('recipes.recipe_id', ondelete='CASCADE'), nullable=False),
        sa.Column('step_no', sa.Integer(), nullable=False),
        sa.Column('instruction_clean', sa.Text(), nullable=False),
        sa.UniqueConstraint('recipe_id', 'step_no', name='uq_recipe_steps_order'),
    )
    op.create_index('ix_recipe_steps_recipe_id', 'recipe_steps', ['recipe_id'])

    op.create_table(
        'recipe_ingredient_aliases',
        sa.Column('alias_id', sa.String(32), primary_key=True),
        sa.Column('ingredient_id', sa.String(32), nullable=False),
        sa.Column('canonical_name', sa.Text(), nullable=False),
        sa.Column('alias', sa.Text(), nullable=False),
    )
    op.create_index('ix_recipe_aliases_ingredient_id', 'recipe_ingredient_aliases', ['ingredient_id'])

    op.create_table(
        'recipe_allergens',
        sa.Column('allergen_id', sa.String(32), primary_key=True),
        sa.Column('allergen_name', sa.Text(), nullable=False),
        sa.Column('reference', sa.Text()),
    )


def downgrade() -> None:
    op.drop_table('recipe_allergens')
    op.drop_index('ix_recipe_aliases_ingredient_id', table_name='recipe_ingredient_aliases')
    op.drop_table('recipe_ingredient_aliases')
    op.drop_index('ix_recipe_steps_recipe_id', table_name='recipe_steps')
    op.drop_table('recipe_steps')
    op.drop_index('ix_recipe_ingredients_ingredient_id', table_name='recipe_ingredients')
    op.drop_index('ix_recipe_ingredients_recipe_id', table_name='recipe_ingredients')
    op.drop_table('recipe_ingredients')
    op.drop_index('ix_recipes_category_meal_type', table_name='recipes')
    op.drop_index('ix_recipes_recipe_name', table_name='recipes')
    op.drop_table('recipes')
    op.drop_table('recipe_source_archive')
