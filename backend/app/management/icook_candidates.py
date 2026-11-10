"""Bind an iCook preview to the editor and save the exact preview as a draft."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from decimal import Decimal

import sqlalchemy as sa

from app.core.config import settings

from .contracts import icook_sources
from .dashboard_read import draft_context
from .models import tables as T
from .service import Management, fail, now


IMPORT_ACTION = 'recipe.icook.import'
PREVIEW_SECONDS = 20 * 60
MAX_SNAPSHOT_BYTES = 256 * 1024
_LOCAL_SECRET = secrets.token_bytes(32)


def _signature(encoded: str) -> str:
    configured = settings.management_icook_preview_secret
    secret = configured.encode() if configured else _LOCAL_SECRET
    return hmac.new(secret, ('icook-preview-v1.' + encoded).encode('ascii'), 'sha256').hexdigest()


def existing_draft(service: Management, url: str):
    commands, audit, proposals = T['commands'], T['audit_events'], T['proposals']
    return service.db.scalar(sa.select(proposals.c.id)
        .join(audit, audit.c.resource_id == proposals.c.id)
        .join(commands, commands.c.id == audit.c.command_id)
        .where(audit.c.action == IMPORT_ACTION, commands.c.receipt['source_url'].as_string() == url,
               proposals.c.state.not_in(('withdrawn','rejected')))
        .order_by(proposals.c.created_at.desc(), proposals.c.id).limit(1))


def preview(service: Management, snapshot: dict) -> dict:
    envelope = {'actor': service.actor, 'expires': int(now().timestamp()) + PREVIEW_SECONDS, 'snapshot': snapshot}
    payload = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_SNAPSHOT_BYTES:
        fail('ICOOK_PAGE_TOO_LARGE', '清整後的食譜超出預覽上限。', 422)
    encoded = base64.urlsafe_b64encode(payload).decode('ascii')
    return {**snapshot, 'preview_id': encoded + '.' + _signature(encoded),
            'servings_count': _portions(snapshot['tables']['recipes'][0]['servings_raw']),
            'existing_proposal_id': existing_draft(service, snapshot['source']['url'])}


def _read_preview(token: str, actor: str) -> dict:
    try:
        encoded, signature = token.rsplit('.', 1)
        if not hmac.compare_digest(signature, _signature(encoded)):
            raise ValueError
        envelope = json.loads(base64.urlsafe_b64decode(encoded))
        if envelope['actor'] != actor:
            raise ValueError
        return envelope
    except (ValueError, KeyError, binascii.Error):
        fail('ICOOK_PREVIEW_INVALID', '預覽無法驗證，請重新預覽食譜。', 422)


def _measurement(value, artifact_id):
    return {'value': value, 'status': 'declared' if value is not None else 'unknown',
            'unknown_reason': None if value is not None else '來源未提供可用數值',
            'evidence_ids': [artifact_id] if value is not None else []}


def _positive(value):
    return value is not None and Decimal(value) > 0


def _portions(raw_servings):
    match = re.fullmatch(r'([1-9][0-9]{0,15})(?:\s*(?:人份|人|份))?', raw_servings or '')
    return match[1] if match else None


def _content(snapshot, artifact):
    rows = snapshot['tables']
    recipe = rows['recipes'][0]
    raw_servings = recipe['servings_raw']
    count = _portions(raw_servings)
    artifact_id = artifact['artifact_id']
    ingredients = []
    for line in rows['recipe_ingredients']:
        grams = line['weight_g'] if _positive(line['weight_g']) else None
        milliliters = line['amount'] if line['unit'] == 'ml' and _positive(line['amount']) else None
        ingredients.append({
            'line_id': f"line-{line['sequence']}", 'food_id': None, 'ingredient_version_id': None,
            'canonical_name': line['canonical_name'], 'raw_text': line['raw_text'],
            'amount': _measurement(grams or milliliters, artifact_id),
            'unit': 'g' if grams else 'ml' if milliliters else None,
            'weight_g': _measurement(grams, artifact_id),
            'weight_basis': 'whole_source_recipe_before_cooking',
        })
    return {
        'logical_recipe_id': None, 'standard_dish_id': None, 'aliases': [],
        'name': recipe['recipe_name'], 'category': recipe['category'], 'cuisine': recipe['Cuisine'],
        'meal_type': recipe['meal_type'], 'declared_allergen_ids': None,
        'servings': {'count': count, 'basis': 'portions', 'raw_text': raw_servings,
                     'portions_per_batch': None, 'evidence_ids': [artifact_id] if count else []},
        'ingredients': ingredients,
        'steps': [{'step_no': row['step_no'], 'instruction': row['instruction']} for row in rows['recipe_steps']],
        'sources': [{'artifact_id': artifact_id, 'artifact_sha256': artifact['sha256'],
                     'origin_kind': 'web_capture', 'source_policy_version_id': None}],
    }


def save_preview(service: Management, key: str, payload: dict):
    token = payload.get('preview_id')
    if set(payload) != {'preview_id'} or not isinstance(token, str) or len(token) > 400_000:
        fail('INVALID_INPUT', '請先預覽 iCook 食譜。', 422)
    envelope = _read_preview(token, service.actor)
    snapshot = envelope['snapshot']
    url = snapshot['source']['url']
    reason = '儲存已確認的 iCook 食譜預覽'

    def action(command_id):
        if envelope['expires'] <= now().timestamp():
            fail('ICOOK_PREVIEW_EXPIRED', '預覽已逾時，請重新預覽食譜。')
        service.require('recipe.read','recipe')
        service.require('artifact.upload','recipe')
        if service.db.bind.dialect.name == 'postgresql':
            service.db.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:url,0))'), {'url':url})
        existing = existing_draft(service, url)
        if existing:
            result = service.get(existing)
            return {'proposal':result,'existing':True,'source_url':url},existing,None,result['content_hash']
        pins = draft_context(service, 'recipe', None, None, None, None)
        evidence = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        child_key = f'icook-{command_id}'
        artifact = service.upload(child_key,'recipe','web_capture','application/json',evidence,reason,recipe_capture=True)
        result = service.create(child_key, {'domain':'recipe','content':_content(snapshot, artifact),
                                'dependency_pins':pins,'reason':reason},recipe_capture=True)
        return {'proposal':result,'existing':False,'source_url':url},result['proposal_id'],None,result['content_hash']

    command_body = {'preview_sha256':hashlib.sha256(token.encode()).hexdigest(),'reason':reason}
    return service.command(IMPORT_ACTION,'recipe',key,command_body,'recipe.edit','recipe',action)


def candidate_evidence(service: Management, pid: str):
    proposal = service.get(pid)
    service.require('artifact.read','recipe')
    captures = icook_sources(proposal['content'])
    if proposal['domain'] != 'recipe' or not captures:
        fail('NOT_FOUND',status=404)
    source = captures[0]
    artifact = service.row('artifacts',T['artifacts'].c.id == source['artifact_id'])
    if not artifact or artifact['domain'] != 'recipe' or artifact['source_kind'] != 'web_capture':
        fail('SOURCE_EVIDENCE_REQUIRED')
    if hashlib.sha256(artifact['payload']).hexdigest() != source['artifact_sha256']:
        fail('SOURCE_EVIDENCE_REQUIRED')
    snapshot = json.loads(artifact['payload'])
    row = service.proposal(pid)
    revision = service.revision(row)
    codes, changed = service.icook_quantities(row,revision)
    return {'source': snapshot['source'], 'source_archive': snapshot['tables']['source_archive'][0],
            'quantity_issues': codes, 'confirmation_required':changed,
            'quantities_confirmed':changed and service.icook_confirmed(row,revision),
            'revision':revision['revision']}
