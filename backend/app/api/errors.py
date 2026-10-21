"""Spec 的穩定錯誤碼與統一回應格式：{"error": {"code", "message", "details"}}。"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def invalid_input(message: str, *, field: str | None = None, reason: str | None = None) -> ApiError:
    details = {k: v for k, v in (("field", field), ("reason", reason)) if v}
    return ApiError(422, "INVALID_INPUT", message, details)


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "請先登入。")


def forbidden(message: str = "沒有存取此資料的權限。") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def not_found(message: str = "找不到資料。") -> ApiError:
    # 跨家庭資源一律回 404，不洩漏其他家庭資料是否存在
    return ApiError(404, "NOT_FOUND", message)


def version_conflict() -> ApiError:
    return ApiError(409, "VERSION_CONFLICT", "資料已被更新，請重新載入後再修改。")


def already_adopted() -> ApiError:
    """T16：已採用（confirmed）的問卷保存特定結果版本與快照，不被新生成或修改覆寫。"""
    return ApiError(409, "VERSION_CONFLICT", "此問卷已採用，不能再生成或修改；請另建新問卷。",
                    {"reason": "ALREADY_ADOPTED"})


def idempotency_conflict(message: str = "這個送出識別碼已用於不同的內容，請重新操作。") -> ApiError:
    """同一 Idempotency-Key 搭配不同命令（request hash 不同）。"""
    return ApiError(409, "IDEMPOTENCY_CONFLICT", message)


def _body(code: str, message: str, details: dict) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
            fields.append({"field": ".".join(loc), "reason": err.get("type"), "message": err.get("msg")})
        # 不回傳原始輸入值，避免把密碼或健康資料帶回回應或日誌
        return JSONResponse(
            status_code=422,
            content=_body("INVALID_INPUT", "輸入資料格式不正確。", {"fields": fields}),
        )
