from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from app.api.deps import verify_origin
from app.api.management import service
from app.management.price_policy import list_observations
from app.management.service import fail
from app.management.contracts import PRICE_EXTENSION, SCHEMAS, VERSION

router = APIRouter(prefix="/admin/v1", tags=["management"], dependencies=[Depends(verify_origin)])
ScopeID = Annotated[str, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")]
UTCInstant = Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")]


@router.get("/contracts/price-v1")
def price_contract(s=Depends(service)):
    s.require("registry.read")
    return {"contract_version": VERSION, "extension": PRICE_EXTENSION, "schemas": SCHEMAS}


@router.get("/price-observations", operation_id="listPriceObservations")
def price_observations(
    recipe_release_id: ScopeID,
    food_id: ScopeID,
    as_of: UTCInstant,
    cursor: Annotated[str | None, Query(max_length=180)] = None,
    include_product_name: bool = False,
    s=Depends(service),
):
    s.require("price.read", "price")
    try:
        observed_by = datetime.fromisoformat(as_of)
        before = None
        if cursor:
            value, snapshot_id = cursor.split("/", 1)
            before = datetime.fromisoformat(value), int(snapshot_id)
            if not before[0].tzinfo or before[0] > observed_by or not 0 < before[1] <= 2**63 - 1:
                raise ValueError("Invalid cursor boundary")
    except (ValueError, OverflowError):
        fail("INVALID_INPUT", status=422)
    if not s.db.scalar(sa.text("SELECT 1 FROM recipe_data_releases WHERE release_id=:id"), {"id": recipe_release_id}):
        fail("NOT_FOUND", status=404)
    return list_observations(s.db, recipe_release_id, food_id, observed_by, before, include_product_name)
