"""Authenticated recipe candidate intake."""

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import current_household_id, verify_origin
from app.api.management import body, complete, key, service
from app.management import generated_recipe_candidates as generated
from app.management.icook_candidates import candidate_evidence, preview, save_preview
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
        result = preview(management, fetch_recipe(payload['url']))
    except ICookError as exc:
        fail(exc.code, str(exc), exc.status)
    management.response.headers['Cache-Control'] = 'no-store'
    return {**result, 'step_checks': check_recipe_steps(
        (step['step_no'], step['instruction']) for step in result['tables']['recipe_steps']
    )}


@router.post('/icook/drafts', status_code=201)
def save_icook(request: Request, payload=Depends(body), management=Depends(service)):
    return complete(management, save_preview(management, key(request), payload))


@router.get('/icook/{proposal_id}/source')
def icook_evidence(proposal_id: str, management=Depends(service)):
    return candidate_evidence(management, proposal_id)


@router.post('/icook/{proposal_id}/confirm-quantities')
def confirm_icook(proposal_id: str, request: Request, payload=Depends(body), management=Depends(service)):
    return complete(management, management.confirm_icook_quantities(key(request), proposal_id, payload))


@router.get('/generated/choices')
def generated_choices(page: int = Query(0, ge=0, le=9999), household_id=Depends(current_household_id), management=Depends(service)):
    return generated.choices(management, household_id, page)


@router.post('/generated/preview')
def preview_generated(payload=Depends(body), household_id=Depends(current_household_id), management=Depends(service)):
    result = generated.preview(management, household_id, payload)
    management.response.headers['Cache-Control'] = 'no-store'
    return {**result, 'step_checks': check_recipe_steps(enumerate(result['snapshot']['steps'], 1))}


@router.post('/generated/drafts', status_code=201)
def save_generated(request: Request, payload=Depends(body), household_id=Depends(current_household_id), management=Depends(service)):
    return complete(management, generated.save_preview(management, household_id, key(request), payload))


@router.get('/generated/{proposal_id}/source')
def generated_evidence(proposal_id: str, management=Depends(service)):
    return generated.candidate_evidence(management, proposal_id)


@router.get('/{proposal_id}/fixture')
def fixture_evidence(proposal_id: str, management=Depends(service)):
    return get_fixture(management, proposal_id)
