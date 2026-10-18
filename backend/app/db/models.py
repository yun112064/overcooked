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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
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


class MealRequest(Base):
    """T08 當餐問卷草稿。payload 為目前內容，每次保存都另存一份不可變的版本。"""
    __tablename__ = "meal_requests"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'queued', 'running', 'awaiting_decision', 'result_ready', "
                        "'failed', 'confirmed')", name="ck_meal_request_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 方便依日期列出草稿；完整內容在 payload
    meal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MealRequestRevision(Base):
    __tablename__ = "meal_request_revisions"

    meal_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BackgroundJob(Base):
    """T10 持久工作。短交易領取並記錄租約；外部呼叫期間不持有資料庫鎖。"""
    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_job_status"),
        UniqueConstraint("household_id", "idempotency_key", name="uq_job_idempotency"),
        Index("ix_background_jobs_claim", "available_at", postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 冪等：同鍵同內容回傳同一工作；request_hash 不同即拒絕
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    input_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetMonth(Base):
    """每月外部服務總額度（新台幣）。committed 為主機、備份等已占用的固定費用。"""
    __tablename__ = "budget_months"
    __table_args__ = (
        CheckConstraint("reserved_twd >= 0 AND spent_twd >= 0 AND committed_twd >= 0", name="ck_budget_nonnegative"),
    )

    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    limit_twd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    committed_twd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    reserved_twd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    spent_twd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UsageReservation(Base):
    """每筆付費呼叫的預留與結算；uncertain 表示可能已計費但結果不明，需人工核對。"""
    __tablename__ = "usage_reservations"
    __table_args__ = (
        CheckConstraint("status IN ('reserved', 'settled', 'released', 'uncertain')", name="ck_usage_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_key: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="SET NULL"), nullable=True
    )
    month: Mapped[str] = mapped_column(String(7), ForeignKey("budget_months.month"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved")
    reserved_twd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    actual_twd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    fx_rate_twd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(40), nullable=False)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
