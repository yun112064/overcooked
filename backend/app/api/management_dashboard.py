"""Dashboard GET adapters using the existing management session and domain grants."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
import sqlalchemy as sa

from app.api.deps import verify_origin
from app.api.management import service
from app.management import dashboard_read as view
from app.management import operations_read
from app.management.models import tables as T
from app.management.service import fail

router = APIRouter(prefix='/admin/v1', tags=['management-dashboard'], dependencies=[Depends(verify_origin)])

Domain = Literal['recipe', 'ingredient', 'price', 'source_policy']
LegacyDomain = Literal['recipe', 'ingredient']
Channel = Literal['recipe_catalog', 'ingredient_catalog', 'price_policy', 'source_policy']
State = Literal['draft', 'changes_requested', 'submitted', 'approved', 'rejected', 'published', 'withdrawn']
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=500)]
ID = Annotated[str, Path(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$')]
QueryID = Annotated[str | None, Query(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$')]
Version = Annotated[int, Path(ge=1, le=2_147_483_647)]
QueryVersion = Annotated[int, Query(ge=1, le=2_147_483_647)]


@router.get('/data-types')
def data_types(limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    s.require('registry.read')
    scope = ['data-types']
    keys = view.decode_cursor(cursor, scope, (str,))
    entries = [item for item in view.DATA_TYPES if not keys or item['key'] > keys[0]]
    return view.page(entries, limit, scope, lambda item: (item['key'],))


@router.get('/data-types/{type_key}')
def data_type(type_key: ID, s=Depends(service)):
    s.require('registry.read')
    entry = next((item for item in view.DATA_TYPES if item['key'] == type_key), None)
    if entry is None:
        fail('NOT_FOUND', status=404)
    return entry


@router.get('/proposals')
def proposals(domain: Domain, state: State | None = None, limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return view.proposals(s, domain, state, limit, cursor)


@router.get('/proposals/{proposal_id}/revisions/{revision}')
def proposal_revision(proposal_id: ID, revision: Version, s=Depends(service)):
    proposal = view.visible_proposal(s, proposal_id)
    return view.proposal_view(proposal, view.revision(s, proposal, revision))


@router.get('/proposals/{proposal_id}/diff')
def proposal_diff(proposal_id: ID, base_revision: QueryVersion, target_revision: QueryVersion,
                  limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    proposal = view.visible_proposal(s, proposal_id)
    return view.proposal_diff(s, proposal, base_revision, target_revision, limit, cursor)


@router.get('/proposals/{proposal_id}/workflow')
def proposal_workflow(proposal_id: ID, include_validation_controls: bool = False, s=Depends(service)):
    return view.proposal_workflow(s, view.visible_proposal(s, proposal_id), include_validation_controls)


@router.get('/proposals/{proposal_id}/review-history')
def review_history(proposal_id: ID, limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return operations_read.review_history(s, view.visible_proposal(s, proposal_id), limit, cursor)


@router.get('/publications')
def publications(domain: Domain, limit: Limit = 50, cursor: Cursor = None,
                 legacy_recipe_release_id: QueryID = None, ingredient_publication_id: QueryID = None, s=Depends(service)):
    return view.publications(s, domain, limit, cursor, legacy_recipe_release_id, ingredient_publication_id)


@router.get('/publications/{publication_id}')
def publication(publication_id: ID, s=Depends(service)):
    t = T['publications']
    row = s.row('publications', t.c.id == publication_id)
    if row is None:
        fail('NOT_FOUND', status=404)
    s.require(row['domain'] + '.read', row['domain'])
    return view.publication_view(row)


@router.get('/bindings/{channel}')
def binding(channel: Channel, include_runtime: bool = False, s=Depends(service)):
    result = view.binding(s, channel)
    if include_runtime and channel == 'recipe_catalog':
        result['runtime_enabled'] = s.binding_enabled(channel)
    return result


@router.get('/draft-context')
def draft_context(domain: Domain, base_publication_id: QueryID = None, ingredient_publication_id: QueryID = None,
                  legacy_recipe_release_id: QueryID = None, proposal_id: QueryID = None, s=Depends(service)):
    return view.draft_context(s, domain, base_publication_id, ingredient_publication_id, legacy_recipe_release_id, proposal_id)


@router.get('/entities/{domain}')
def entities(domain: Domain, view_kind: Literal['publication', 'legacy_recipe_release'],
             view_id: Annotated[str, Query(min_length=1, max_length=100)], limit: Limit = 50,
             cursor: Cursor = None, s=Depends(service)):
    if view_kind != 'publication':
        s.require(domain + '.read', domain)
        fail('LEGACY_VIEW_NOT_AVAILABLE', '舊版資料請使用未審核的舊版目錄檢視。', 503)
    return view.entities(s, domain, view_id, limit=limit, cursor=cursor)


@router.get('/entities/{domain}/{logical_id}')
def entity(domain: Domain, logical_id: ID, view_kind: Literal['publication', 'legacy_recipe_release'],
           view_id: Annotated[str, Query(min_length=1, max_length=100)], s=Depends(service)):
    if view_kind != 'publication':
        s.require(domain + '.read', domain)
        fail('LEGACY_VIEW_NOT_AVAILABLE', '舊版資料請使用未審核的舊版目錄檢視。', 503)
    return view.entities(s, domain, view_id, logical_id=logical_id)


@router.get('/legacy-catalog/{domain}/releases')
def legacy_releases(domain: LegacyDomain, limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return view.legacy_releases(s, domain, limit, cursor)


@router.get('/price-releases')
def price_releases(limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return view.legacy_releases(s, 'price', limit, cursor)


@router.get('/legacy-catalog/{domain}/{release_id}')
def legacy_entities(domain: LegacyDomain, release_id: ID, limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return view.legacy_entities(s, domain, release_id, limit, cursor)


@router.get('/legacy-catalog/{domain}/{release_id}/{logical_id}')
def legacy_entity(domain: LegacyDomain, release_id: ID, logical_id: ID, s=Depends(service)):
    return view.legacy_entity(s, domain, release_id, logical_id)


@router.get('/tasks')
def tasks(limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return view.tasks(s, limit, cursor)


@router.get('/metrics/summary')
def metrics(s=Depends(service)):
    return view.metrics(s)


@router.get('/overview')
def overview(s=Depends(service)):
    return operations_read.overview(s)


@router.get('/database-status')
def database_status(s=Depends(service)):
    return operations_read.database_status(s)


@router.get('/meal-confirmation-flows')
def meal_confirmation_flows(response: Response, limit: Annotated[int, Query(ge=1, le=30)] = 20, s=Depends(service)):
    response.headers['Cache-Control'] = 'no-store'
    return operations_read.meal_confirmation_flows(s, limit)


@router.get('/schedule-configuration')
def schedule_configuration(s=Depends(service)):
    return operations_read.schedule_configuration(s)


@router.get('/schedule-price-runs')
def schedule_price_runs(limit: Limit = 20, s=Depends(service)):
    return operations_read.scheduled_price_runs(s, limit)


@router.get('/audit-events')
def audit_events(domain: Domain | None = None, limit: Limit = 50, cursor: Cursor = None, s=Depends(service)):
    return operations_read.audit_events(s, domain, limit, cursor)


@router.get('/connections')
def connections(s=Depends(service)):
    return view.connections(s)


@router.get('/connections/{registered_target_key}')
def connection(registered_target_key: ID, s=Depends(service)):
    row = next((item for item in view.connections(s)['items'] if item['registered_target_key'] == registered_target_key), None)
    if row is None:
        fail('NOT_FOUND', status=404)
    return row
