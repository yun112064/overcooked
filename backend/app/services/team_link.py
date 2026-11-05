"""T17 雲端↔團隊電腦連線的共用規則（YOLO 辨識、知識 RAG 轉接器與團隊電腦呼叫雲端內部 API 共用）。

服務經 Cloudflare Tunnel 對外，前面有 Cloudflare Access：呼叫端帶 Access 服務權杖標頭
（CF-Access-Client-Id／CF-Access-Client-Secret）通過 Access，再帶服務自己的 Bearer token 通過服務驗證。
加密由 Cloudflare 的 HTTPS 與 tunnel 負責；兩端都不開任何對外連入埠。
"""
from dataclasses import dataclass

# Cloudflare 在 tunnel 另一端（cloudflared）離線時回 530（error 1033），源站連不上時回 502／504
TUNNEL_DOWN_STATUSES = {502, 504, 530}


@dataclass(frozen=True)
class TeamCredentials:
    """服務 Bearer token＋Cloudflare Access 服務權杖（兩者都可能未設定）。repr 不含秘密。"""

    token: str | None
    access_client_id: str | None = None
    access_client_secret: str | None = None

    def headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if self.access_client_id and self.access_client_secret:
            headers["CF-Access-Client-Id"] = self.access_client_id
            headers["CF-Access-Client-Secret"] = self.access_client_secret
        return headers

    def __repr__(self) -> str:
        return (f"TeamCredentials(token={'set' if self.token else None}, "
                f"access={'set' if self.access_client_id and self.access_client_secret else None})")


def settings_credentials(token: str | None) -> TeamCredentials:
    """雲端呼叫團隊電腦：服務 token＋設定中的 Access 服務權杖（兩個服務共用）。"""
    from app.core.config import settings
    return TeamCredentials(token, settings.team_access_client_id, settings.team_access_client_secret)


def failure_reason(status_code: int) -> str | None:
    """非 200 回應對應的不可用原因；200 回 None。Access 缺權杖時會導向登入頁（3xx），視為驗證失敗。"""
    if status_code == 200:
        return None
    if status_code in (401, 403) or 300 <= status_code < 400:
        return "AUTH_FAILED"
    if status_code in TUNNEL_DOWN_STATUSES:
        return "UNREACHABLE"
    return "SERVICE_ERROR"
