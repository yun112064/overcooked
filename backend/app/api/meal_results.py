"""T15 結果讀取與缺值決策 API（掛在 /meal-requests/{id}/results 底下）。

採用（POST /meal-plans）與歷史在 T16；這裡只提供可查看的結果、後端判定的採用資格與決定紀錄。
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import not_found
from app.api.meal_requests import get_owned_request
from app.db.models import GenerationResult, MealRequest
from app.db.session import get_db
from app.services import jobs
from app.services import meal_results as service

router = APIRouter(prefix="/meal-requests", tags=["meal-results"], dependencies=[Depends(verify_origin)])


class DecisionBody(BaseModel):
    kind: Literal["accept_missing_price", "continue_missing_nutrition"]
    request_version: int = Field(ge=1)


class RetryBody(BaseModel):
    request_version: int = Field(ge=1)


def _result(db: Session, request: MealRequest, version: int) -> GenerationResult:
    row = db.scalar(select(GenerationResult).where(GenerationResult.meal_request_id == request.id,
                                                   GenerationResult.result_version == version))
    if row is None:
        raise not_found("找不到此結果版本。")
    return row


@router.get("/{request_id}/results")
def list_results(request_id: uuid.UUID, household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    request = get_owned_request(db, household_id, request_id)
    return {"results": service.summaries(db, request)}


@router.get("/{request_id}/results/{result_version}")
def get_result(request_id: uuid.UUID, result_version: int, household_id=Depends(current_household_id),
               db: Session = Depends(get_db)):
    request = get_owned_request(db, household_id, request_id)
    return service.view(db, request, _result(db, request, result_version))


@router.post("/{request_id}/results/{result_version}/decisions")
def post_decision(request_id: uuid.UUID, result_version: int, body: DecisionBody, response: Response,
                  idempotency_key: str = Header(min_length=8, max_length=100),
                  auth: AuthContext = Depends(current_auth), household_id=Depends(current_household_id),
                  db: Session = Depends(get_db)):
    """接受缺價例外／缺必要營養繼續查看。不放寬禁忌、指定菜、±10% 或昨日去重；採用資格仍由後端判定。"""
    request = get_owned_request(db, household_id, request_id, lock=True)
    result = _result(db, request, result_version)
    decision, created = service.decide(db, household_id=household_id, request=request, result=result,
                                       kind=body.kind, request_version=body.request_version,
                                       idempotency_key=idempotency_key, user_id=auth.user_id)
    db.commit()
    response.status_code = 201 if created else 200
    return {"decision": service.public_decision(db, decision), "result": service.view(db, request, result)}


def _retry(request_id, result_version, body, response, idempotency_key, household_id, db, submit):
    request = get_owned_request(db, household_id, request_id, lock=True)
    result = _result(db, request, result_version)
    job, created = submit(db, household_id=household_id, request=request, result=result,
                          request_version=body.request_version, idempotency_key=idempotency_key)
    db.commit()
    response.status_code = 202 if created else 200
    return {"job": jobs.public_view(job), "created": created}


@router.post("/{request_id}/results/{result_version}/price-retry")
def post_price_retry(request_id: uuid.UUID, result_version: int, body: RetryBody, response: Response,
                     idempotency_key: str = Header(min_length=8, max_length=100),
                     household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """缺價重試（P3）：缺價食材排入 T02 定向查價背景工作，完成後產生新結果版本（菜單與營養不變）。"""
    return _retry(request_id, result_version, body, response, idempotency_key, household_id, db,
                  service.request_price_retry)


@router.post("/{request_id}/results/{result_version}/knowledge-retry")
def post_knowledge_retry(request_id: uuid.UUID, result_version: int, body: RetryBody, response: Response,
                         idempotency_key: str = Header(min_length=8, max_length=100),
                         household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """只重試知識說明（pre-review #3）：同一結果的最終食材重新檢索知識並產生說明，產生新結果版本。"""
    return _retry(request_id, result_version, body, response, idempotency_key, household_id, db,
                  service.request_knowledge_retry)
