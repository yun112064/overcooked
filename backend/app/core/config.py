from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 專案根目錄（backend/app/core/config.py 往上三層）
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_url: str

    # 登入 session：只存在伺服器端，Cookie 僅帶隨機 token
    session_cookie_name: str = "mp_session"
    session_ttl_hours: int = 24 * 7
    # 本機 http 開發可設 false；正式環境必須維持 true
    cookie_secure: bool = True

    # 允許的前端來源（狀態修改請求的 Origin／Referer 檢查），逗號分隔
    allowed_origins: str = "http://localhost:8080,http://localhost:5173"

    # 同義食材對照（不修改工作簿；過敏全套用、其他只用已確認組）
    ingredient_synonyms_path: Path = PROJECT_ROOT / "config/ingredient_synonyms.json"

    nutrition_rules_path: Path = (
        PROJECT_ROOT / "nutrition_target/rules/hpa-dri8-mvp-0.3.0.json"
    )

    # T10 外部服務費用：每月總上限包含主機、模型、備份等全部服務
    monthly_budget_twd: Decimal = Decimal("2000")
    # 規劃分配：主機與稅費 1,000＋備份／網域／匯率餘裕 400；正式採購後依實際帳單更新
    committed_services_twd: Decimal = Decimal("1400")
    # 美元換算新台幣的保守估計；結算時記錄於每筆 usage，帳單到手後核對
    usd_twd_rate: Decimal = Decimal("33")
    model_pricing_path: Path = PROJECT_ROOT / "config/model_pricing.json"
    # 每戶每日最多送出的生成工作數
    generation_daily_limit: int = 20
    job_lease_seconds: int = 300
    job_retry_backoff_seconds: int = 30
    worker_poll_seconds: float = 2.0
    # 日誌清理預設由單一排程程序負責；API 程序內清理僅供未啟用排程時使用
    log_cleanup_in_api: bool = False

    # T09 上傳與團隊電腦 YOLO 推論服務
    project_data_root: Path = Path("/app/data")
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_max_pixels: int = 40_000_000
    # 未設定代表推論服務不可用（顯示服務狀態，可手動輸入），不是「沒有結果」
    recognition_service_url: str | None = None
    recognition_service_token: str | None = None
    recognition_timeout_seconds: float = 10.0
    recognition_min_confidence: Decimal = Decimal("0.25")
    recognition_labels_path: Path = PROJECT_ROOT / "config/recognition_labels.json"

    # T11 團隊電腦營養知識 RAG 與 OpenAI 向量化
    # 未設定代表知識服務不可用（KNOWLEDGE_UNAVAILABLE），不是「沒有知識」
    knowledge_service_url: str | None = None
    knowledge_service_token: str | None = None
    knowledge_timeout_seconds: float = 10.0
    knowledge_limit_per_ingredient: int = 3
    # 未設定 API key 代表付費模型不可用；一律經 T10 額度預留後才呼叫
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 30.0
    embedding_model: str = "text-embedding-3-small"
    # T14 菜單生成／AI 回退與知識說明：固定版本模型（decisions #11），輸出上限即預留的最壞情況
    generation_model: str = "gpt-5.4-mini-2026-03-17"
    generation_timeout_seconds: float = 90.0
    generation_max_output_tokens: int = 6000
    explanation_max_output_tokens: int = 2000

    # T17 團隊電腦服務經 Cloudflare Tunnel＋Access：雲端呼叫時附上的 Access 服務權杖（兩個服務共用）
    team_access_client_id: str | None = None
    team_access_client_secret: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_origin_set(self) -> set[str]:
        return {
            origin.strip().rstrip("/")
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        }


settings = Settings()
