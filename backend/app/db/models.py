"""T07 身份、家庭與成員資料表。

家庭成員是被規劃的對象，不需要登入帳號；帳號透過 household_memberships
取得家庭存取權。所有家庭資源都由伺服器依 session 判斷歸屬。
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)




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
    # 工作參數（T15 重試工作：來源結果 ID、要查價的 FOOD 編號）；不含個人資料
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class Recognition(Base):
    """T09 蔬菜辨識紀錄。畫面列出所有辨識到的蔬菜；偵測明細保留供評估與追溯。"""
    __tablename__ = "recognitions"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'no_result', 'unavailable')", name="ck_recognition_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Data Root 內的相對路徑；圖片只經家庭授權的 API 讀取
    image_path: Mapped[str] = mapped_column(String(200), nullable=False)
    image_width: Mapped[int] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, nullable=False)
    top_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    top_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    top_confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detections: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    unavailable_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 使用者修正後確認的 [{name, ingredient_id}]，一張照片可確認多種（AC22 不以此計分）
    confirmed_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdoptedMealPlan(Base):
    """T11 已採用餐食紀錄（最小快照）：昨日去重的讀取來源；T16 擴充採用交易與完整快照。

    dishes 為 [{standard_dish_id, dish_name, aliases, source, recipe_id}]，資料庫菜與 AI 菜都保存識別。
    只有 status='adopted' 才算已採用歷史；生成、查看或未採用的結果不會寫入此處。
    """
    __tablename__ = "adopted_meal_plans"
    __table_args__ = (
        CheckConstraint("meal_type IN ('breakfast', 'lunch', 'dinner')", name="ck_adopted_meal_type"),
        CheckConstraint("mode IN ('database_menu', 'ai_generated_menu')", name="ck_adopted_meal_mode"),
        CheckConstraint("status IN ('adopted', 'superseded')", name="ck_adopted_meal_status"),
        CheckConstraint("version > 0", name="ck_adopted_meal_version_positive"),
        Index("ix_adopted_meal_plans_household_date", "household_id", "meal_date"),
        # T16 採用交易：冪等鍵、同一問卷只採用一次
        UniqueConstraint("household_id", "idempotency_key", name="uq_adopted_meal_plan_idempotency"),
        UniqueConstraint("meal_request_id", name="uq_adopted_meal_plan_request"),
        CheckConstraint("(idempotency_key IS NULL) = (request_hash IS NULL)", name="ck_adopted_meal_idempotency_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    meal_date: Mapped[date] = mapped_column(Date, nullable=False)
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="adopted")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    dishes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # T16 擴充（T11 的最小紀錄這些欄位為 NULL／預設）：採用版本、冪等與 MealPlanSnapshot。
    # 問卷或結果之後刪除時改為 NULL，歷史快照保留。
    meal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="SET NULL",
                                       name="fk_adopted_meal_plans_meal_request"), nullable=True
    )
    generation_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_results.id", ondelete="SET NULL",
                                       name="fk_adopted_meal_plans_generation_result"), nullable=True
    )
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_exception_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                           server_default=text("false"))
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    adopted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", name="fk_adopted_meal_plans_adopted_by"),
        nullable=True
    )


class GenerationResult(Base):
    """T14 生成結果（GenerationResult 契約）：每個成功的生成工作恰好一筆，不覆寫、不刪改。

    - result_version：同一問卷內依序遞增（1, 2, …），重新生成或修改問卷後再生成都是新版本；
      T15 顯示、T16 採用以 (meal_request_id, result_version) 指定版本。
    - job_id 唯一：同一送出（同一冪等鍵）只會產生一個結果，重送不會多寫。
    - request_version：產生此結果的問卷鎖定版本。
    - payload：完整結果快照（菜色、查詢來源、計算、價格、昨日比對、知識說明與證據）。
    """
    __tablename__ = "generation_results"
    __table_args__ = (
        CheckConstraint("mode IN ('database_menu', 'ai_generated_menu')", name="ck_generation_result_mode"),
        CheckConstraint("(mode = 'database_menu') = (fallback_reason IS NULL)",
                        name="ck_generation_result_fallback_reason"),
        CheckConstraint("result_version > 0 AND request_version > 0", name="ck_generation_result_versions"),
        UniqueConstraint("job_id", name="uq_generation_result_job"),
        UniqueConstraint("meal_request_id", "result_version", name="uq_generation_result_version"),
        CheckConstraint("job_id IS NOT NULL OR derived_from_result_id IS NOT NULL", name="ck_generation_result_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meal_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False
    )
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # T15：重試（只重試知識說明、缺價定向查價）產生的新版本以 derived_from_result_id 指向來源版本；
    # 由重試工作產生時 job_id 也有值
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=True
    )
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    derived_from_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=True
    )


class MealResultDecision(Base):
    """T15 使用者對單一結果版本的決定（R09）；T16 採用時重新讀取並以 meal_validation 重驗。

    - accept_missing_price：明確接受缺價例外；details.missing_food_ids 保存當時缺價食材集合，
      集合不同（例如重試後又變）就不算數。已知成本超預算時不可建立。
    - continue_missing_nutrition：確認在缺必要營養下繼續查看；不會讓結果變成可採用。
    - retry_missing_price：重讀價格快照產生新結果版本；produced_result_id 指向新版本。
    冪等：(household_id, idempotency_key) 唯一＋request_hash；同一結果同一種決定只有一筆。
    """
    __tablename__ = "meal_result_decisions"
    __table_args__ = (
        CheckConstraint("kind IN ('accept_missing_price', 'continue_missing_nutrition', 'retry_missing_price')",
                        name="ck_meal_result_decision_kind"),
        UniqueConstraint("household_id", "idempotency_key", name="uq_meal_result_decision_idempotency"),
        UniqueConstraint("generation_result_id", "kind", name="uq_meal_result_decision_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meal_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meal_requests.id", ondelete="CASCADE"), nullable=False
    )
    generation_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=False
    )
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    produced_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class KnowledgeCrawlRequest(Base):
    """G07 待補知識食材佇列（雲端）：最終食材缺審核知識時記錄 FOOD 編號，供團隊電腦爬蟲優先處理。

    只存 FOOD 編號、次數與時間，不存家庭、使用者或問卷資料。團隊電腦經雲端內部 API 認領與回報
    （不直接連雲端資料庫）；認領逾時（claimed_at 過舊）的請求可再被認領。
    """
    __tablename__ = "knowledge_crawl_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'claimed', 'crawled')", name="ck_knowledge_crawl_request_status"),
        CheckConstraint("food_id ~ '^FOOD[0-9]{6}$'", name="ck_knowledge_crawl_request_food_id"),
        Index("ix_knowledge_crawl_requests_pending", "status", "request_count"),
    )

    food_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outcome: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversionFactorEstimate(Base):
    """decisions #19 換算係數快取：烹調產率／保留率（kind=cooking）與每單位克數（kind=unit）。

    批次指令（python -m app.estimation）以 gpt-5.4-mini 事先推估寫入；生成時只查表。
    同一鍵可有多列（重新推估、人工覆寫各新增一列）；取用見 app.estimation.store.load_snapshot。
    """
    __tablename__ = "conversion_factor_estimates"
    __table_args__ = (
        CheckConstraint("kind IN ('cooking', 'unit', 'tfda_mapping')", name="ck_conversion_factor_kind"),
        CheckConstraint("source IN ('tfda', 'human', 'llm_estimated')", name="ck_conversion_factor_source"),
        CheckConstraint("status IN ('llm_estimated', 'approved', 'rejected', 'overridden')",
                        name="ck_conversion_factor_status"),
        CheckConstraint(
            "(kind = 'cooking' AND grams_per_unit IS NULL AND energy_retention IS NOT NULL "
            "AND protein_retention IS NOT NULL) OR "
            "(kind = 'unit' AND grams_per_unit IS NOT NULL AND yield_factor IS NULL "
            "AND energy_retention IS NULL AND protein_retention IS NULL) OR "
            "(kind = 'tfda_mapping' AND grams_per_unit IS NULL AND yield_factor IS NULL "
            "AND energy_retention IS NULL AND protein_retention IS NULL)",
            name="ck_conversion_factor_values"),
        CheckConstraint("tfda_sample_id IS NULL OR kind = 'tfda_mapping'", name="ck_conversion_factor_tfda_sample"),
        CheckConstraint("yield_factor IS NULL OR (yield_factor > 0 AND yield_factor <= 10)",
                        name="ck_conversion_factor_yield_range"),
        CheckConstraint("energy_retention IS NULL OR (energy_retention >= 0 AND energy_retention <= 1)",
                        name="ck_conversion_factor_energy_retention_range"),
        CheckConstraint("protein_retention IS NULL OR (protein_retention >= 0 AND protein_retention <= 1)",
                        name="ck_conversion_factor_protein_retention_range"),
        CheckConstraint("grams_per_unit IS NULL OR grams_per_unit > 0", name="ck_conversion_factor_grams_positive"),
        CheckConstraint("source <> 'llm_estimated' OR (model IS NOT NULL AND prompt_version IS NOT NULL "
                        "AND schema_version IS NOT NULL)", name="ck_conversion_factor_llm_trace"),
        Index("ix_conversion_factor_estimates_key", "kind", "food_id", "factor_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    food_id: Mapped[str] = mapped_column(String(32), nullable=False)
    factor_key: Mapped[str] = mapped_column(String(100), nullable=False)
    food_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    yield_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    energy_retention: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    protein_retention: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    grams_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                                 server_default=text("'{}'::jsonb"))
    assumptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    call_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # decisions #28（kind=tfda_mapping）：對照到的 TFDA 樣本 ID；NULL 代表模型回答「無合適樣本」
    tfda_sample_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
