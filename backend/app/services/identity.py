"""IdentityService：帳號、Argon2 密碼雜湊與伺服器端 session。"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import User, UserSession

_hasher = PasswordHasher()
# 帳號不存在時也做一次雜湊比對，避免以回應時間判斷帳號是否存在
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_session(db: Session, user: User) -> str:
    """建立 session，回傳只放在 Cookie 的原始 token；資料庫只存其雜湊。"""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.add(UserSession(
        token_hash=_token_hash(token),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
    ))
    return token


def find_session(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    row = db.get(UserSession, _token_hash(token))
    if row is None:
        return None
    if row.expires_at <= datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return row


def delete_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))
