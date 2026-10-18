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

    nutrition_rules_path: Path = (
        PROJECT_ROOT / "nutrition_target/rules/hpa-dri8-mvp-0.3.0.json"
    )
    ingredient_master_path: Path = (
        PROJECT_ROOT / "nutrition_model/data/ingredient_id_master.json"
    )

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
