import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, verify_origin
from app.api.errors import ApiError
from app.core.config import settings
from app.core.logging_config import api_logger
from app.db.models import Household, User
from app.db.session import get_db
from app.services import identity

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(verify_origin)])

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        value = identity.normalize_email(value)
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("請輸入有效的 Email。")
        return value


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _me(db: Session, auth_user_id, household_id) -> dict:
    user = db.get(User, auth_user_id)
    household = db.get(Household, household_id) if household_id else None
    return {
        "user": {"id": str(user.id), "email": user.email},
        "current_household": (
            {"id": str(household.id), "name": household.name} if household else None
        ),
    }


@router.post("/register", status_code=201)
def register(body: Credentials, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise ApiError(409, "INVALID_INPUT", "此 Email 已註冊，請直接登入。",
                       {"field": "email", "reason": "EMAIL_TAKEN"})
    user = User(email=body.email, password_hash=identity.hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "INVALID_INPUT", "此 Email 已註冊，請直接登入。",
                       {"field": "email", "reason": "EMAIL_TAKEN"}) from None
    token = identity.create_session(db, user)
    db.commit()
    _set_session_cookie(response, token)
    return _me(db, user.id, None)


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    user = identity.authenticate(db, body.email, body.password)
    if user is None:
        api_logger.warning("Login failed", extra={"event": "auth_login_failed", "source": "api"})
        raise ApiError(401, "UNAUTHENTICATED", "Email 或密碼不正確。")
    token = identity.create_session(db, user)
    db.commit()
    _set_session_cookie(response, token)
    return _me(db, user.id, None)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    identity.delete_session(db, request.cookies.get(settings.session_cookie_name))
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/",
                           secure=settings.cookie_secure, httponly=True, samesite="lax")
    response.status_code = 204
    return response


@router.get("/me")
def me(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    return _me(db, auth.user_id, auth.session.household_id)
