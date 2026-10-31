from decimal import Decimal
from pathlib import Path
from typing import Literal

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

    # T11 defaults to team-computer Ollama embeddings; the legacy paid key stays on the cloud.
    openai_api_key: str | None = None
    knowledge_provider: Literal['ollama','openai'] = 'ollama'
    knowledge_service_url: str | None = None
    knowledge_service_token: str | None = None
    knowledge_internal_token: str | None = None

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
