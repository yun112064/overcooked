import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_household_id, verify_origin
from app.api.errors import ApiError, invalid_input, not_found
from app.core.config import settings
from app.db.models import MealRequest, Recognition
from app.db.session import get_db
from app.services import ingredients
from app.services import recognition as service

router = APIRouter(prefix="/recognitions", tags=["recognitions"], dependencies=[Depends(verify_origin)])

UNAVAILABLE_MESSAGES = {
    "NOT_CONFIGURED": "辨識服務目前未啟用，請直接手動輸入食材。",
    "UNREACHABLE": "暫時連不到辨識服務（團隊電腦可能離線），照片已保留，可稍後重試或手動輸入。",
    "TIMEOUT": "辨識逾時，照片已保留，可重試或手動輸入。",
    "AUTH_FAILED": "辨識服務驗證失敗，請通知管理者；可先手動輸入。",
    "SERVICE_ERROR": "辨識服務發生錯誤，照片已保留，可重試或手動輸入。",
    "INVALID_RESPONSE": "辨識服務回應異常，照片已保留，可重試或手動輸入。",
}


def get_recognizer():
    """測試可覆寫此相依注入假的推論服務。"""
    return service.default_recognizer()


class ConfirmBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ingredient_id: str = Field(min_length=1, max_length=20)


def _out(r: Recognition) -> dict:
    if r.status == "succeeded":
        message = "請確認或修改辨識出的蔬菜名稱，逐一加入家中已有食材。"
    elif r.status == "no_result":
        message = "沒有辨識到蔬菜，請手動輸入名稱。"
    else:
        message = UNAVAILABLE_MESSAGES.get(r.unavailable_reason, UNAVAILABLE_MESSAGES["SERVICE_ERROR"])
    return {
        "id": str(r.id),
        "status": r.status,
        "message": message,
        # 每種辨識到的蔬菜一筆，依信心由高到低；不回傳信心與框線
        "suggestions": service.suggestions(r.detections) if r.status == "succeeded" else [],
        "retryable": r.status == "unavailable",
        "confirmed": r.confirmed_items,
        "image_url": f"/recognitions/{r.id}/image",
        "meal_request_id": str(r.meal_request_id) if r.meal_request_id else None,
    }


def _owned(db: Session, household_id, recognition_id) -> Recognition:
    row = db.get(Recognition, recognition_id)
    if row is None or row.household_id != household_id:
        raise not_found("找不到此辨識紀錄。")
    return row


@router.post("", status_code=201)
async def create_recognition(
    image: UploadFile = File(...),
    meal_request_id: uuid.UUID | None = Form(None),
    household_id=Depends(current_household_id),
    db: Session = Depends(get_db),
    recognizer=Depends(get_recognizer),
):
    raw = await image.read(settings.upload_max_bytes + 1)
    jpeg, width, height = service.normalize_image(raw)
    if meal_request_id is not None:
        owner = db.get(MealRequest, meal_request_id)
        if owner is None or owner.household_id != household_id:
            raise invalid_input("找不到此問卷。", field="meal_request_id", reason="UNKNOWN_MEAL_REQUEST")
    rid = uuid.uuid4()
    path = service.save_image(household_id, rid, jpeg)
    fields = service.run(recognizer, jpeg)
    row = Recognition(id=rid, household_id=household_id, meal_request_id=meal_request_id,
                      image_path=path, image_width=width, image_height=height, **fields)
    db.add(row)
    db.commit()
    return _out(row)


@router.get("/{recognition_id}")
def get_recognition(recognition_id: uuid.UUID, household_id=Depends(current_household_id),
                    db: Session = Depends(get_db)):
    return _out(_owned(db, household_id, recognition_id))


@router.get("/{recognition_id}/image")
def get_image(recognition_id: uuid.UUID, household_id=Depends(current_household_id),
              db: Session = Depends(get_db)):
    row = _owned(db, household_id, recognition_id)
    try:
        path = service.image_file(row.image_path)
    except FileNotFoundError:
        raise not_found("找不到照片。") from None
    if not path.is_file():
        raise not_found("找不到照片。")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/{recognition_id}/retry")
def retry_recognition(recognition_id: uuid.UUID, household_id=Depends(current_household_id),
                      db: Session = Depends(get_db), recognizer=Depends(get_recognizer)):
    row = _owned(db, household_id, recognition_id)
    if row.status != "unavailable":
        raise ApiError(409, "VERSION_CONFLICT", "只有服務不可用的辨識可以重試。", {"reason": "NOT_RETRYABLE"})
    try:
        jpeg = service.image_file(row.image_path).read_bytes()
    except FileNotFoundError:
        raise not_found("找不到照片，請重新上傳。") from None
    for key, value in service.run(recognizer, jpeg).items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _out(row)


@router.post("/{recognition_id}/confirm")
def confirm_recognition(recognition_id: uuid.UUID, body: ConfirmBody,
                        household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """加入一筆使用者修正後的名稱與標準食材；只確認種類，不含重量。同一標準食材重複確認時以最後一次為準。"""
    row = _owned(db, household_id, recognition_id)
    if ingredients.unknown_ids(db, [body.ingredient_id]):
        raise invalid_input("請從標準食材清單選擇。", field="ingredient_id", reason="UNKNOWN_INGREDIENT_ID")
    items = [i for i in row.confirmed_items if i["ingredient_id"] != body.ingredient_id]
    row.confirmed_items = [*items, {"name": body.name.strip(), "ingredient_id": body.ingredient_id}]
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _out(row)


@router.delete("/{recognition_id}", status_code=204)
def delete_recognition(recognition_id: uuid.UUID, household_id=Depends(current_household_id),
                       db: Session = Depends(get_db)):
    row = _owned(db, household_id, recognition_id)
    try:
        service.image_file(row.image_path).unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    db.delete(row)
    db.commit()
    return Response(status_code=204)
