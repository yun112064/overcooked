"""Copy one authorized T14 dish into an editable management draft."""

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import UUID

import sqlalchemy as sa

from app.db.models import GenerationResult, HouseholdMembership

from .contracts import recipe_capture_sources
from .dashboard_read import draft_context
from .models import tables as T
from .service import Management, fail, stamp


IMPORT_ACTION = 'recipe.ai_result.import'
PAGE_SIZE = 15
AI_MENU = 'ai_generated_menu'


def _owner(service: Management, household_id: UUID, *, lock=False):
    query = sa.select(HouseholdMembership.id).where(
        HouseholdMembership.household_id == household_id,
        HouseholdMembership.user_id == UUID(service.actor),
        HouseholdMembership.role == 'owner',
    )
    if lock:
        query = query.with_for_update(read=True)
    if service.db.scalar(query) is None:
        fail('AI_RECIPE_HOUSEHOLD_OWNER_REQUIRED', '僅目前家庭的擁有者可將菜色帶入管理後台。', 403)


def _permissions(service: Management, household_id: UUID, *, lock=False):
    for permission in ('recipe.read', 'recipe.edit', 'artifact.upload'):
        service.require(permission, 'recipe')
    _owner(service, household_id, lock=lock)


def _dishes(payload):
    dishes = payload.get('dishes')
    if isinstance(dishes, list):
        for index, dish in enumerate(dishes):
            if isinstance(dish, dict) and dish.get('source') == 'ai':
                yield index, dish


def choices(service: Management, household_id: UUID, page: int):
    _permissions(service, household_id)
    results = service.db.execute(sa.select(
        GenerationResult.id, GenerationResult.result_version, GenerationResult.created_at, GenerationResult.payload,
    ).where(GenerationResult.household_id == household_id, GenerationResult.mode == AI_MENU)
        .order_by(GenerationResult.created_at.desc(), GenerationResult.id.desc())
        .offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1)).all()
    return {
        'items': [
            {'result_id': str(row.id), 'result_version': row.result_version, 'created_at': stamp(row.created_at),
             'dish_index': index, 'name': dish['dish_name']}
            for row in results[:PAGE_SIZE] for index, dish in _dishes(row.payload)
            if isinstance(dish.get('dish_name'), str) and dish['dish_name'].strip()
        ],
        'has_next': len(results) > PAGE_SIZE,
    }


def _invalid_source():
    fail('AI_RECIPE_SOURCE_INVALID', '生成結果中的菜色缺少可匯入的食材、每份克數或步驟。', 422)


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _invalid_source()
    return value


def _grams(value):
    # T14 stores Decimal as a string, preserving its original precision.
    if not isinstance(value, str) or len(value) > 100:
        _invalid_source()
    try:
        grams = Decimal(value)
    except InvalidOperation:
        _invalid_source()
    if not grams.is_finite() or grams <= 0:
        _invalid_source()
    return value


def _snapshot(service: Management, household_id: UUID, result_id: UUID, dish_index: int):
    result = service.db.scalar(sa.select(GenerationResult).where(
        GenerationResult.id == result_id, GenerationResult.household_id == household_id,
        GenerationResult.mode == AI_MENU,
    ))
    dish = dict(_dishes(result.payload)).get(dish_index) if result else None
    if dish is None:
        fail('NOT_FOUND', '找不到這個家庭的 AI 菜色。', 404)
    ingredients, steps = dish.get('ingredients'), dish.get('steps')
    if (not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 200
            or not isinstance(steps, list) or not 1 <= len(steps) <= 200):
        _invalid_source()
    lines = []
    for line in ingredients:
        if not isinstance(line, dict):
            _invalid_source()
        food_id = _text(line.get('food_id'), 100)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}', food_id):
            _invalid_source()
        lines.append({'food_id': food_id, 'name': _text(line.get('name'), 200),
                      'grams_per_n': _grams(line.get('grams_per_n')),
                      'weight_basis': line.get('weight_basis') if line.get('weight_basis') in ('raw', 'cooked') else None})
    evidence = result.payload.get('evidence') or {}
    return {
        'format_version': 'ai-result-dish-v1',
        'source': {
            'result_id': str(result.id), 'result_version': result.result_version, 'dish_index': dish_index,
            'created_at': stamp(result.created_at), 'mode': result.mode,
            **{field: evidence.get(field) for field in ('menu_model', 'menu_prompt_version', 'menu_schema_version')
               if isinstance(evidence.get(field), str)},
        },
        'name': _text(dish.get('dish_name'), 200),
        'ingredients': lines,
        'steps': [_text(step, 2000) for step in steps],
    }


def _snapshot_bytes(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _selection(payload):
    try:
        result_id = UUID(payload['result_id'])
        dish_index = payload['dish_index']
        if type(dish_index) is not int or not 0 <= dish_index < 200:
            raise ValueError
    except (ValueError, KeyError, AttributeError, TypeError):
        fail('INVALID_INPUT', '請選擇一筆 AI 菜色。', 422)
    return result_id, dish_index


def _existing(service: Management, scope: str):
    commands, audit, proposals = T['commands'], T['audit_events'], T['proposals']
    return service.db.scalar(sa.select(proposals.c.id)
        .join(audit, audit.c.resource_id == proposals.c.id)
        .join(commands, commands.c.id == audit.c.command_id)
        .where(commands.c.operation == IMPORT_ACTION, commands.c.resource_scope == scope,
               proposals.c.state.not_in(('withdrawn', 'rejected')))
        .order_by(proposals.c.created_at.desc(), proposals.c.id).limit(1))


def preview(service: Management, household_id: UUID, payload: dict):
    _permissions(service, household_id)
    if set(payload) != {'result_id', 'dish_index'}:
        fail('INVALID_INPUT', '請選擇一筆 AI 菜色。', 422)
    result_id, dish_index = _selection(payload)
    snapshot = _snapshot(service, household_id, result_id, dish_index)
    return {'snapshot': snapshot, 'snapshot_sha256': hashlib.sha256(_snapshot_bytes(snapshot)).hexdigest(),
            'existing_proposal_id': _existing(service, f'{result_id}:{dish_index}')}


def _content(snapshot, artifact):
    unknown = {'value': None, 'status': 'unknown', 'unknown_reason': '整份食譜用量待確認', 'evidence_ids': []}
    return {
        'logical_recipe_id': None, 'standard_dish_id': None, 'aliases': [], 'name': snapshot['name'],
        'category': None, 'cuisine': None, 'meal_type': None, 'declared_allergen_ids': None,
        'servings': {'count': None, 'basis': 'portions', 'raw_text': None, 'portions_per_batch': None, 'evidence_ids': []},
        'ingredients': [
            {'line_id': f'line-{i}', 'food_id': line['food_id'], 'ingredient_version_id': None,
             'canonical_name': line['name'], 'raw_text': f"{line['name']} {line['grams_per_n']} g／n=1",
             'amount': unknown, 'unit': None, 'weight_g': unknown,
             'weight_basis': 'whole_source_recipe_before_cooking'}
            for i, line in enumerate(snapshot['ingredients'], 1)
        ],
        'steps': [{'step_no': i, 'instruction': step} for i, step in enumerate(snapshot['steps'], 1)],
        'sources': [{'artifact_id': artifact['artifact_id'], 'artifact_sha256': artifact['sha256'],
                     'origin_kind': 'ai_output', 'source_policy_version_id': None}],
    }


def save_preview(service: Management, household_id: UUID, key: str, payload: dict):
    _permissions(service, household_id, lock=True)
    result_id, dish_index = _selection(payload)
    expected = payload.get('snapshot_sha256')
    if set(payload) != {'result_id', 'dish_index', 'snapshot_sha256'} or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        fail('INVALID_INPUT', '請先預覽 AI 菜色。', 422)
    scope = f'{result_id}:{dish_index}'
    reason = '儲存已確認的家庭 AI 菜色預覽'

    # Check the household on every request, including command replays.
    snapshot = _snapshot(service, household_id, result_id, dish_index)
    evidence = _snapshot_bytes(snapshot)
    if hashlib.sha256(evidence).hexdigest() != expected:
        fail('AI_RECIPE_PREVIEW_CHANGED', '來源與預覽不同，請重新預覽。')

    def action(command_id):
        if service.db.bind.dialect.name == 'postgresql':
            service.db.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'), {'scope':'management:ai-result:' + scope})
        existing = _existing(service, scope)
        if existing:
            result = service.get(existing)
            return {'proposal':result,'existing':True},existing,None,result['content_hash']
        pins = draft_context(service, 'recipe', None, None, None, None)
        child_key = f'ai-result-{command_id}'
        artifact = service.upload(child_key, 'recipe', 'ai_output', 'application/json', evidence, reason, recipe_capture=True)
        result = service.create(child_key, {'domain':'recipe','content':_content(snapshot, artifact),
                                'dependency_pins':pins,'reason':reason}, recipe_capture=True)
        return {'proposal':result,'existing':False},result['proposal_id'],None,result['content_hash']

    return service.command(IMPORT_ACTION, scope, key, {'snapshot_sha256': expected, 'reason': reason},
                           'recipe.edit', 'recipe', action)


def candidate_evidence(service: Management, pid: str):
    proposal = service.get(pid)
    service.require('artifact.read', 'recipe')
    captures = recipe_capture_sources(proposal['content'], ('ai_output',))
    if proposal['domain'] != 'recipe' or not captures:
        fail('NOT_FOUND', status=404)
    source = captures[0]
    artifact = service.row('artifacts', T['artifacts'].c.id == source['artifact_id'])
    if not artifact or artifact['domain'] != 'recipe' or artifact['source_kind'] != 'ai_output':
        fail('SOURCE_EVIDENCE_REQUIRED')
    if hashlib.sha256(artifact['payload']).hexdigest() != source['artifact_sha256']:
        fail('SOURCE_EVIDENCE_REQUIRED')
    return json.loads(artifact['payload'])
