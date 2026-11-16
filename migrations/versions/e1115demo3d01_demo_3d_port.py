"""3D 前端移植：選單拒絕、封面與 T04 營養觀測資料表（只新增資料表，不改動既有資料表）

新增資料表（依外鍵順序）：
- meal_menu_rejections：「不採用，換一份」，同一問卷版本下次查詢時略過這組食譜。
- recipe_data_releases：食譜資料發布版本；只作為營養發布與封面的外鍵來源，本遷移不寫入任何資料列，
  也不在 recipes 等 T01 資料表新增 recipe_release_id。
- nutrition_imports / nutrition_sources / food_nutrition / nutrition_source_links：版本化營養觀測；
  七項營養素皆可為 NULL、沒有預設值，未知不會變成 0。
- nutrition_data_releases：營養來源批次與食譜發布版本的明確綁定。
- nutrition_product_sources / nutrition_source_candidates：補充來源證據，皆待審核，不是核准的計算對照。
- recipe_covers：核准的食譜封面圖（image_bytes 為 bytea）。

本遷移只有純 DDL（不連線查詢、不做條件判斷），可用 alembic --sql 離線產生 SQL。

警告：downgrade() 會無條件刪除上述所有資料表，連同已載入的 T04 營養證據、封面與選單拒絕紀錄
（DEMO 測試的 migrate_fresh 需要能完整降版）。絕對不要在已有資料的資料庫上執行 downgrade；
需要回復時，請從升級前的 ops.backup 備份還原。

Revision ID: e1115demo3d01
Revises: d4e8a2c7f913
Create Date: 2026-11-15 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e1115demo3d01"
down_revision: Union[str, Sequence[str], None] = "d4e8a2c7f913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugars_g", "sodium_mg", "dietary_fiber_g")


def upgrade() -> None:
    op.create_table(
        "meal_menu_rejections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meal_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("recipe_ids", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_meal_menu_rejections_request", "meal_menu_rejections",
                    ["meal_request_id", "request_version"])

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
            "source_kind IN ('legacy', 'sql_seed', 'xlsx_import')",
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

    op.create_table(
        "nutrition_imports",
        sa.Column("import_id", sa.String(64), primary_key=True),
        sa.Column("source_files", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "nutrition_sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_sheet", sa.String(100), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "source_key", name="uq_nutrition_source_key"),
    )
    op.create_index("ix_nutrition_sources_import_id", "nutrition_sources", ["import_id"])
    op.create_table(
        "food_nutrition",
        sa.Column("nutrition_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("standard_name", sa.Text(), nullable=False),
        sa.Column("cooking_state", sa.Text(), nullable=False),
        sa.Column("cooking_method", sa.Text()),
        sa.Column("preparation", sa.Text()),
        sa.Column("calculation_method", sa.Text()),
        # 七項營養素皆為每 100 g；缺值保持 NULL，不設預設值
        *(sa.Column(field, sa.Numeric(24, 12)) for field in NUTRIENTS),
        sa.Column("nutrition_complete", sa.Boolean(), nullable=False),
        sa.Column("quality_issues", sa.JSON(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "source_row", name="uq_food_nutrition_row"),
        *(sa.CheckConstraint(f"{field} IS NULL OR {field} >= 0", name=f"ck_food_nutrition_{field}")
          for field in NUTRIENTS),
    )
    op.create_index("ix_food_nutrition_import_id", "food_nutrition", ["import_id"])
    op.create_index("ix_food_nutrition_food_id", "food_nutrition", ["food_id"])
    op.create_table(
        "nutrition_source_links",
        sa.Column("nutrition_id", sa.String(64), sa.ForeignKey("food_nutrition.nutrition_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("nutrition_sources.source_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("included_in_calculation", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "nutrition_data_releases",
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("nutrition_imports.import_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("recipe_release_id", sa.String(64),
                  sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipe_source_sha256", sa.String(64), nullable=False),
        sa.Column("mapping_version", sa.String(100), nullable=False, unique=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("row_counts", postgresql.JSONB(), nullable=False),
        sa.Column("usage_status", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("usage_status = 'observations_only_pending_review'", name="ck_nutrition_release_usage"),
    )
    op.create_index("uq_nutrition_release_active", "nutrition_data_releases", ["recipe_release_id"],
                    unique=True, postgresql_where=sa.text("active"))

    op.create_table(
        "nutrition_product_sources",
        sa.Column("product_source_id", sa.String(64), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64),
                  sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("per_100g", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "nutrition_source_candidates",
        sa.Column("candidate_id", sa.String(64), primary_key=True),
        sa.Column("recipe_release_id", sa.String(64),
                  sa.ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("nutrition_import_id", sa.String(64),
                  sa.ForeignKey("nutrition_imports.import_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("food_id", sa.String(32), sa.ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("nutrition_sources.source_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("match_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("review_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("recipe_release_id", "nutrition_import_id", "food_id", "source_id", "policy_version",
                            name="uq_nutrition_source_candidate"),
        sa.CheckConstraint("review_status = 'pending_review'", name="ck_nutrition_candidate_review"),
    )

    op.create_table(
        "recipe_covers",
        sa.Column("recipe_id", sa.String(32), sa.ForeignKey("recipes.recipe_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("recipe_release_id", sa.String(64), sa.ForeignKey("recipe_data_releases.release_id"),
                  primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("media_type IN ('image/jpeg', 'image/png', 'image/webp')",
                           name="ck_recipe_covers_media_type"),
    )


def downgrade() -> None:
    # 無條件刪除，連同已載入的資料；只可用於空的測試資料庫（見模組說明）
    op.drop_table("recipe_covers")
    op.drop_table("nutrition_source_candidates")
    op.drop_table("nutrition_product_sources")
    op.drop_index("uq_nutrition_release_active", table_name="nutrition_data_releases")
    op.drop_table("nutrition_data_releases")
    op.drop_table("nutrition_source_links")
    op.drop_index("ix_food_nutrition_food_id", table_name="food_nutrition")
    op.drop_index("ix_food_nutrition_import_id", table_name="food_nutrition")
    op.drop_table("food_nutrition")
    op.drop_index("ix_nutrition_sources_import_id", table_name="nutrition_sources")
    op.drop_table("nutrition_sources")
    op.drop_table("nutrition_imports")
    op.drop_index("uq_recipe_data_releases_active", table_name="recipe_data_releases")
    op.drop_table("recipe_data_releases")
    op.drop_index("ix_meal_menu_rejections_request", table_name="meal_menu_rejections")
    op.drop_table("meal_menu_rejections")
