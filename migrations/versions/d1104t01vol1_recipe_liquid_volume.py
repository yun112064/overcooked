"""Store optional recipe volume alongside the existing pre-cooking mass."""

from alembic import op
import sqlalchemy as sa


revision = "d1104t01vol1"
down_revision = "d1104t02mass1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recipe_ingredients", sa.Column("volume_ml", sa.Numeric(16, 6)))
    op.add_column("recipe_ingredients", sa.Column("volume_evidence", sa.Text()))
    op.execute("""
        UPDATE recipe_ingredients
        SET volume_ml = amount * CASE WHEN unit IN ('L', '公升') THEN 1000 ELSE 1 END,
            volume_evidence = 'workbook_volume'
        WHERE unit IN ('ml', 'L', '公升') AND amount > 0
    """)
    op.execute("""
        UPDATE recipe_ingredients
        SET volume_ml = amount * 15,
            volume_evidence = 'estimate:https://icook.tw/recipes/' ||
                CASE WHEN recipe_id = 'R0016' THEN '181145' ELSE '98073' END ||
                '; standard tablespoon 15 mL'
        WHERE recipe_release_id = 'recipe-1103-v3-dd4f75c13772'
          AND ((recipe_id = 'R0016' AND recipe_ingredient_id IN ('RI000106', 'RI000108', 'RI000110') AND amount = 3)
            OR (recipe_id = 'R0017' AND recipe_ingredient_id = 'RI000119' AND amount = 3)
            OR (recipe_id = 'R0017' AND recipe_ingredient_id = 'RI000120' AND amount = 1)
            OR (recipe_id = 'R0017' AND recipe_ingredient_id = 'RI000121' AND amount = 0.5))
          AND unit = '大匙' AND volume_ml IS NULL
    """)


def downgrade():
    op.drop_column("recipe_ingredients", "volume_evidence")
    op.drop_column("recipe_ingredients", "volume_ml")
