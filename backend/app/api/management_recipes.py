"""Authenticated adapter for fixture-only recipe candidates."""

from fastapi import APIRouter, Depends, Request

from app.api.deps import verify_origin
from app.api.management import body, complete, key, service
from app.management.recipe_candidates import get_fixture, import_fixture


router = APIRouter(prefix='/admin/v1/recipe-candidates', tags=['management'], dependencies=[Depends(verify_origin)])


@router.post('/fixtures', status_code=201)
def create_fixture(request: Request, payload=Depends(body), management=Depends(service)):
    return complete(management, import_fixture(management, key(request), payload))


@router.get('/{proposal_id}/fixture')
def fixture_evidence(proposal_id: str, management=Depends(service)):
    return get_fixture(management, proposal_id)
