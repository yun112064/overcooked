from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.session import get_db
from app.models.recipe import RecipeCover

router = APIRouter(tags=["recipe-covers"])


@router.get("/recipe-covers/{release_id}/{recipe_id}")
def get_cover(release_id: str, recipe_id: str, db: Session = Depends(get_db)):
    cover = db.get(RecipeCover, (recipe_id, release_id))
    if cover is None:
        raise not_found("找不到食譜封面。")
    return Response(
        content=cover.image_bytes,
        media_type=cover.media_type,
        headers={"ETag": f'"{cover.sha256}"', "Cache-Control": "public, max-age=3600",
                 "X-Content-Type-Options": "nosniff"},
    )
