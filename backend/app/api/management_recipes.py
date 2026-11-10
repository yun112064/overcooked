"""Authenticated entry points for recipe candidate sources."""

from fastapi import APIRouter, Depends, Request

from app.api.deps import verify_origin
from app.api.management import body, complete, key, service
from app.management.recipe_candidates import get_fixture, import_fixture, preview_fixture_steps
from app.management.service import fail
from app.services.recipe_step_style import check_recipe_steps
from app.source_adapters.icook import ICookError, fetch_recipe


router = APIRouter(prefix='/admin/v1/recipe-candidates', tags=['management'], dependencies=[Depends(verify_origin)])


@router.post('/fixtures', status_code=201)
def create_fixture(request: Request, payload=Depends(body), management=Depends(service)):
    return complete(management, import_fixture(management, key(request), payload))


@router.post('/fixtures/preview')
def preview_fixture(payload=Depends(body), management=Depends(service)):
    management.require('recipe.read', 'recipe')
    management.require('recipe.edit', 'recipe')
    management.response.headers['Cache-Control'] = 'no-store'
    return preview_fixture_steps(payload)


@router.post('/icook/preview')
def preview_icook(payload=Depends(body), management=Depends(service)):
    management.require('recipe.read', 'recipe')
    management.require('recipe.edit', 'recipe')
    if set(payload) != {'url'} or not isinstance(payload['url'], str):
        fail('INVALID_INPUT', '請提供一個 iCook 食譜網址。', 422)
    try:
        result = fetch_recipe(payload['url'])
    except ICookError as exc:
        fail(exc.code, str(exc), exc.status)
    management.response.headers['Cache-Control'] = 'no-store'
    return {**result, 'step_checks': check_recipe_steps(
        (step['step_no'], step['instruction']) for step in result['tables']['recipe_steps']
    )}


@router.get('/{proposal_id}/fixture')
def fixture_evidence(proposal_id: str, management=Depends(service)):
    return get_fixture(management, proposal_id)
