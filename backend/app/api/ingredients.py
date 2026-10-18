from fastapi import APIRouter, Depends, Query

from app.api.deps import current_auth
from app.services import ingredients

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


@router.get("", dependencies=[Depends(current_auth)])
def search_ingredients(q: str = Query("", max_length=50), limit: int = Query(20, ge=1, le=50)):
    """標準食材字典搜尋，供過敏／禁吃及已有食材種類選擇。"""
    return {"ingredients": ingredients.search(q, limit)}
