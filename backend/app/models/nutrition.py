"""Versioned nutrition observations; FOOD identifies a food, not an observation."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NutritionImport(Base):
    __tablename__ = "nutrition_imports"

    import_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_files: Mapped[dict] = mapped_column(JSON, nullable=False)
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NutritionSource(Base):
    __tablename__ = "nutrition_sources"
    __table_args__ = (UniqueConstraint("import_id", "source_key", name="uq_nutrition_source_key"),)

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_sheet: Mapped[str] = mapped_column(String(100), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    # Exact source cells, including numeric values, evidence and exclusion reasons.
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)


class FoodNutrition(Base):
    __tablename__ = "food_nutrition"
    __table_args__ = (
        UniqueConstraint("import_id", "source_row", name="uq_food_nutrition_row"),
        *(CheckConstraint(f"{field} IS NULL OR {field} >= 0", name=f"ck_food_nutrition_{field}")
          for field in ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugars_g", "sodium_mg", "dietary_fiber_g")),
    )

    nutrition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False, index=True)
    food_id: Mapped[str] = mapped_column(ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), nullable=False, index=True)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    standard_name: Mapped[str] = mapped_column(Text, nullable=False)
    cooking_state: Mapped[str] = mapped_column(Text, nullable=False)
    cooking_method: Mapped[str | None] = mapped_column(Text)
    preparation: Mapped[str | None] = mapped_column(Text)
    calculation_method: Mapped[str | None] = mapped_column(Text)
    # All seven values are per 100 g; missing observations remain NULL.
    energy_kcal: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    carbohydrate_g: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    sugars_g: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    dietary_fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    nutrition_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality_issues: Mapped[list] = mapped_column(JSON, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)


class NutritionSourceLink(Base):
    __tablename__ = "nutrition_source_links"

    nutrition_id: Mapped[str] = mapped_column(ForeignKey("food_nutrition.nutrition_id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("nutrition_sources.source_id", ondelete="CASCADE"), primary_key=True)
    included_in_calculation: Mapped[bool] = mapped_column(Boolean, nullable=False)


class NutritionFoodMapping(Base):
    """Version-pinned Food_ID to TFDA observation candidates; pending by default."""
    __tablename__ = "nutrition_food_mappings"
    __table_args__ = (
        UniqueConstraint("import_id", "recipe_release_id", "food_id", "nutrition_id", "source_id",
                         "mapping_version",
                         name="uq_nutrition_food_mapping"),
        CheckConstraint("approval_status IN ('pending_review','approved','rejected')",
                        name="ck_nutrition_food_mapping_status"),
    )

    mapping_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("nutrition_imports.import_id", ondelete="CASCADE"), nullable=False, index=True)
    recipe_release_id: Mapped[str] = mapped_column(ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False, index=True)
    food_id: Mapped[str] = mapped_column(ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), nullable=False, index=True)
    nutrition_id: Mapped[str] = mapped_column(ForeignKey("food_nutrition.nutrition_id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("nutrition_sources.source_id", ondelete="CASCADE"), nullable=False)
    cooking_state: Mapped[str] = mapped_column(Text, nullable=False)
    cooking_method: Mapped[str | None] = mapped_column(Text)
    preparation: Mapped[str | None] = mapped_column(Text)
    approval_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review", server_default="pending_review")
    mapping_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)


class T04CookingReceipt(Base):
    """Immutable, version-bound cooking parameters consumed by formal T12."""
    __tablename__ = "t04_cooking_receipts"
    __table_args__ = (
        UniqueConstraint("input_digest", "source_version", name="uq_t04_receipt_input_version"),
        Index("ix_t04_receipt_lookup", "recipe_release_id", "nutrition_import_id", "mapping_version", "recipe_ingredient_id"),
        CheckConstraint("weight_basis IN ('raw','cooked')", name="ck_t04_receipt_weight_basis"),
        CheckConstraint("yield_factor > 0 AND yield_factor <= 10", name="ck_t04_receipt_yield"),
    )

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recipe_release_id: Mapped[str] = mapped_column(ForeignKey("recipe_data_releases.release_id", ondelete="RESTRICT"), nullable=False)
    nutrition_import_id: Mapped[str] = mapped_column(ForeignKey("nutrition_imports.import_id", ondelete="RESTRICT"), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recipe_ingredient_id: Mapped[str] = mapped_column(String(32), nullable=False)
    food_id: Mapped[str] = mapped_column(String(32), nullable=False)
    nutrition_id: Mapped[str] = mapped_column(ForeignKey("food_nutrition.nutrition_id", ondelete="RESTRICT"), nullable=False)
    nutrition_source_id: Mapped[str] = mapped_column(ForeignKey("nutrition_sources.source_id", ondelete="RESTRICT"), nullable=False)
    sample_id: Mapped[str] = mapped_column(String(100), nullable=False)
    cooking_state: Mapped[str] = mapped_column(Text, nullable=False)
    cooking_method: Mapped[str] = mapped_column(Text, nullable=False)
    weight_basis: Mapped[str] = mapped_column(String(12), nullable=False)
    weight_g: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    yield_factor: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    retention: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assumptions: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    reference_ids: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
