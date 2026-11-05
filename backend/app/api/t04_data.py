"""Household-authorized, read-only T04 source feedback."""
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.api.deps import current_household_id, verify_origin
from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import get_db
from app.db.models import MealRequest
from app.services.t04_data_feedback import recipe_feedback

router=APIRouter(prefix='/meal-requests',tags=['nutrition-data'],dependencies=[Depends(verify_origin)])


class DataRequest(BaseModel):
    request_version:int=Field(ge=1)
    recipe_ids:list[str]=Field(min_length=1,max_length=10)


@router.post('/{request_id}/t04-data')
def data_feedback(request_id:uuid.UUID,body:DataRequest,household_id=Depends(current_household_id),db:Session=Depends(get_db)):
    try:
        row=db.scalar(select(MealRequest).where(MealRequest.id==request_id,MealRequest.household_id==household_id))
        if row is None: raise ApiError(404,'NOT_FOUND','找不到這份問卷。')
        if row.version!=body.request_version: raise ApiError(409,'VERSION_CONFLICT','問卷已更新，請重新檢查營養資料。')
        result=recipe_feedback(db,list(dict.fromkeys(body.recipe_ids)),
            recipe_release_id=settings.generation_recipe_release_id,
            import_id=settings.generation_nutrition_import_id,mapping_version=settings.generation_food_mapping_version)
        return {**result,'input_version':row.version}
    except ValueError as exc:
        raise ApiError(422,str(exc),'食譜或營養來源版本無法使用，請重新確認。') from None
    except SQLAlchemyError:
        raise ApiError(503,'NUTRITION_DATA_UNAVAILABLE','營養來源暫時無法讀取，請稍後重試。') from None
