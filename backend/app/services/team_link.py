"""T17 雲端→團隊電腦連線的共用規則（YOLO 辨識與知識 RAG 轉接器共用）。

團隊電腦服務經 Cloudflare Tunnel 對外，前面有 Cloudflare Access：雲端帶 Access 服務權杖標頭
（CF-Access-Client-Id／CF-Access-Client-Secret）通過 Access，再帶服務自己的 Bearer token 通過服務驗證。
加密由 Cloudflare 的 HTTPS 與 tunnel 負責；團隊電腦不開任何對外連入埠。
"""

# Cloudflare 在 tunnel 另一端（cloudflared）離線時回 530（error 1033），源站連不上時回 502／504
TUNNEL_DOWN_STATUSES = {502, 504, 530}


def team_headers(token: str | None, access_client_id: str | None, access_client_secret: str | None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    return headers


def failure_reason(status_code: int) -> str | None:
    """非 200 回應對應的不可用原因；200 回 None。Access 缺權杖時會導向登入頁（3xx），視為驗證失敗。"""
    if status_code == 200:
        return None
    if status_code in (401, 403) or 300 <= status_code < 400:
        return "AUTH_FAILED"
    if status_code in TUNNEL_DOWN_STATUSES:
        return "UNREACHABLE"
    return "SERVICE_ERROR"
