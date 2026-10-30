"""請求相依：登入身份、目前家庭授權與跨站請求防護。"""
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import ApiError, forbidden, unauthenticated
from app.core.config import settings
from app.db.models import UserSession
from app.db.session import get_db
from app.services import identity
from app.services.household import is_member_of

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def verify_origin(request: Request) -> None:
    """狀態修改請求必須來自允許的前端來源（搭配 SameSite Cookie 防 CSRF）。"""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        origin = _origin_of(referer) if referer else None
    if origin is None or origin.rstrip("/") not in settings.allowed_origin_set:
        raise ApiError(403, "FORBIDDEN", "請求來源未被允許。", {"reason": "ORIGIN_NOT_ALLOWED"})


@dataclass
class AuthContext:
    session: UserSession
    token: str

    @property
    def user_id(self):
        return self.session.user_id


def current_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(settings.session_cookie_name)
    row = identity.find_session(db, token)
    if row is None:
        raise unauthenticated()
    return AuthContext(session=row, token=token)


def current_household_id(
    auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)
):
    """家庭 ID 只取自 session，並每次重新確認帳號仍有該家庭權限。"""
    household_id = auth.session.household_id
    if household_id is None:
        raise ApiError(409, "HOUSEHOLD_NOT_SELECTED", "請先建立或選擇家庭。")
    if not is_member_of(db, auth.user_id, household_id):
        raise forbidden()
    return household_id
