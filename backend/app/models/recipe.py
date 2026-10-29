from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _json_list_default() -> list[Any]:
    return []


class SourceArchive(Base):
    __tablename__ = "source_archive"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "source_recipe_id", name="uq_source_archive_external_recipe"
        ),
    )

    raw_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_recipe_id: Mapped[str] = mapped_column(String(200), nullable=False)
    recipe_name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    servings_raw: Mapped[str] = mapped_column(String(100), nullable=False)
    ingredients_raw: Mapped[str] = mapped_column(Text, nullable=False)
    steps_raw: Mapped[str] = mapped_column(Text, nullable=False)
    tags_raw: Mapped[str] = mapped_column(Text, nullable=False)

    license_name: Mapped[str | None] = mapped_column(String(200))
    rights_uri: Mapped[str | None] = mapped_column(Text)
    rights_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    import_batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    quality_issues: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint("original_servings IS NULL OR original_servings > 0", name="ck_recipe_servings_positive"),
        Index("ix_recipes_standard_dish_id", "standard_dish_id"),
        Index("ix_recipes_eligible", "is_eligible"),
    )

    recipe_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    raw_id: Mapped[str] = mapped_column(
        ForeignKey("source_archive.raw_id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    standard_dish_id: Mapped[str] = mapped_column(String(32), nullable=False)
    recipe_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    name_aliases: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    servings_raw: Mapped[str] = mapped_column(String(100), nullable=False)
    original_servings: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    servings_status: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(100), nullable=False)
    cuisine: Mapped[str] = mapped_column(String(100), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    allergens: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    cooking_methods: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    cooking_method_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unreviewed", server_default="unreviewed"
    )
    duplicate_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unique_candidate", server_default="unique_candidate"
    )
    duplicate_of_recipe_id: Mapped[str | None] = mapped_column(
        ForeignKey("recipes.recipe_id", ondelete="SET NULL")
    )
    duplicate_candidate_recipe_ids: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    quality_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_qualified", server_default="not_qualified"
    )
    is_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    quality_issues: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )


class RecipeStep(Base):
    __tablename__ = "recipe_steps"
    __table_args__ = (
        UniqueConstraint("recipe_id", "step_no", name="uq_recipe_step_number"),
        CheckConstraint("step_no > 0", name="ck_recipe_step_positive"),
    )

    step_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(
        ForeignKey("recipes.recipe_id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction_clean: Mapped[str] = mapped_column(Text, nullable=False)
    cooking_method: Mapped[str | None] = mapped_column(String(100))
    method_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unreviewed", server_default="unreviewed"
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )


class IngredientAlias(Base):
    __tablename__ = "ingredient_aliases"

    food_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    ambiguous_aliases: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    quality_issues: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )


class Allergen(Base):
    __tablename__ = "allergens"

    allergen_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    allergen_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(200))
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        UniqueConstraint("recipe_id", "sequence", name="uq_recipe_ingredient_sequence"),
        CheckConstraint("sequence > 0", name="ck_recipe_ingredient_sequence_positive"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_recipe_ingredient_amount_nonnegative"),
        CheckConstraint("amount_min IS NULL OR amount_min >= 0", name="ck_recipe_ingredient_amount_min_nonnegative"),
        CheckConstraint("amount_max IS NULL OR amount_max >= 0", name="ck_recipe_ingredient_amount_max_nonnegative"),
        CheckConstraint(
            "amount_min IS NULL OR amount_max IS NULL OR amount_min <= amount_max",
            name="ck_recipe_ingredient_amount_range",
        ),
        CheckConstraint("weight_g IS NULL OR weight_g >= 0", name="ck_recipe_ingredient_weight_nonnegative"),
        CheckConstraint(
            "adjustable_min IS NULL OR adjustable_max IS NULL OR adjustable_min <= adjustable_max",
            name="ck_recipe_ingredient_adjustable_range",
        ),
    )

    recipe_ingredient_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(
        ForeignKey("recipes.recipe_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    food_id: Mapped[str] = mapped_column(
        ForeignKey("ingredient_aliases.food_id", ondelete="RESTRICT"), nullable=False, index=True
    )

    source_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    source_unit: Mapped[str | None] = mapped_column(String(100))
    source_weight_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    unit: Mapped[str | None] = mapped_column(String(50))
    quantity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_status: Mapped[str] = mapped_column(String(32), nullable=False)
    weight_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    weight_status: Mapped[str] = mapped_column(String(32), nullable=False)
    adjustable_min: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    adjustable_max: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    adjustment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_reviewed", server_default="not_reviewed"
    )
    is_required: Mapped[bool | None] = mapped_column(Boolean)

    source_protein_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    source_carbohydrates_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    source_fat_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    source_sugar_g: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    source_sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    allergens: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )
    quality_issues: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=_json_list_default, server_default=text("'[]'")
    )

