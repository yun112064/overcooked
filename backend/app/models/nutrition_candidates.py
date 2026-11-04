"""Additional source evidence; never an approved calculation mapping."""
from datetime import datetime
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class NutritionSourceCandidate(Base):
    __tablename__='nutrition_source_candidates'
    __table_args__=(
        UniqueConstraint('recipe_release_id','nutrition_import_id','food_id','source_id','policy_version',
                         name='uq_nutrition_source_candidate'),
        CheckConstraint("review_status = 'pending_review'",name='ck_nutrition_candidate_review'),
    )
    candidate_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    recipe_release_id: Mapped[str]=mapped_column(ForeignKey('recipe_data_releases.release_id',ondelete='RESTRICT'),nullable=False)
    nutrition_import_id: Mapped[str]=mapped_column(ForeignKey('nutrition_imports.import_id',ondelete='RESTRICT'),nullable=False)
    food_id: Mapped[str]=mapped_column(ForeignKey('ingredient_aliases.food_id',ondelete='RESTRICT'),nullable=False)
    source_id: Mapped[str]=mapped_column(ForeignKey('nutrition_sources.source_id',ondelete='RESTRICT'),nullable=False)
    source_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    policy_version: Mapped[str]=mapped_column(String(64),nullable=False)
    match_evidence: Mapped[dict]=mapped_column(JSON().with_variant(JSONB(),'postgresql'),nullable=False)
    review_status: Mapped[str]=mapped_column(String(24),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=text('CURRENT_TIMESTAMP'))


class NutritionProductSource(Base):
    """Product label transcription, separate from official TFDA observations."""
    __tablename__='nutrition_product_sources'
    product_source_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    recipe_release_id: Mapped[str]=mapped_column(ForeignKey('recipe_data_releases.release_id',ondelete='RESTRICT'),nullable=False)
    food_id: Mapped[str]=mapped_column(ForeignKey('ingredient_aliases.food_id',ondelete='RESTRICT'),nullable=False)
    source_name: Mapped[str]=mapped_column(String(200),nullable=False)
    source_url: Mapped[str]=mapped_column(Text,nullable=False)
    evidence_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    per_100g: Mapped[dict]=mapped_column(JSON().with_variant(JSONB(),'postgresql'),nullable=False)
    provenance: Mapped[dict]=mapped_column(JSON().with_variant(JSONB(),'postgresql'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=text('CURRENT_TIMESTAMP'))
