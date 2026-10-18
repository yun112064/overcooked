"""T07 身份、家庭與成員資料表。

家庭成員是被規劃的對象，不需要登入帳號；帳號透過 household_memberships
取得家庭存取權。所有家庭資源都由伺服器依 session 判斷歸屬。
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 一律存小寫，唯一
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Household(Base):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HouseholdMembership(Base):
    __tablename__ = "household_memberships"
    __table_args__ = (UniqueConstraint("user_id", "household_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    # 只保存 token 的 SHA-256，資料庫外洩也無法直接冒用 Cookie
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 目前選擇的家庭；權限仍以 membership 為準，每次請求重新檢查
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Member(Base):
    __tablename__ = "household_members"
    __table_args__ = (
        CheckConstraint("portion IN (0.70, 0.85, 1.00, 1.15, 1.30)", name="ck_member_portion"),
        CheckConstraint("equation_sex IN ('M', 'F')", name="ck_member_equation_sex"),
        CheckConstraint(
            "activity_category IN ('sedentary', 'low_active', 'active', 'highly_active')",
            name="ck_member_activity",
        ),
        CheckConstraint(
            "goal IN ('general', 'maintain', 'muscle_gain', 'fat_loss')", name="ck_member_goal"
        ),
        CheckConstraint("weight_kg > 0", name="ck_member_weight"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # 存出生日期，依計算日期推算足歲，避免年齡隨時間失準
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    equation_sex: Mapped[str] = mapped_column(String(1), nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    activity_category: Mapped[str] = mapped_column(String(20), nullable=False)
    goal: Mapped[str] = mapped_column(String(20), nullable=False)
    portion: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    # 標準食材 ID（ING 編號）清單
    allergen_ingredient_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    avoided_ingredient_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
