"""Local recipe fixture ingestion through the rc1 management workflow."""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.recipe import Recipe

from .contracts import validate
from .models import tables as T
from .service import Management, fail


IMPORT_ACTION = 'recipe_fixture.import'
MAX_FIXTURE_BYTES = 256 * 1024
MAX_SUGGESTIONS = 20


class FixtureFields(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class FixtureAI(FixtureFields):
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)


class FixtureSource(FixtureFields):
    kind: Literal['ai_fixture', 'supplied_document']
    reference: str = Field(min_length=1, max_length=2000)
    observed_at: str | None = Field(default=None, max_length=40)
    source_version: str | None = Field(default=None, min_length=1, max_length=200)
    ai_metadata: FixtureAI | None = None

    @field_validator('observed_at')
    @classmethod
    def require_timezone(cls, value):
        if value is not None and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('observed_at requires a timezone')
        return value

    @model_validator(mode='after')
    def require_ai_metadata(self):
        if (self.kind == 'ai_fixture') != (self.ai_metadata is not None):
            raise ValueError('AI metadata is required for AI fixtures only')
        return self


class FixtureServings(FixtureFields):
    basis: Literal['portions', 'batch']
    raw_text: str
    count: str | None = None
    portions_per_batch: str | None = None


class FixtureLine(FixtureFields):
    raw_text: str
    canonical_name: str
    food_id: str | None = None
    ingredient_version_id: str | None = None
    amount: str | None = None
    unit: str | None = None
    weight_g: str | None = None


class FixtureRecipe(FixtureFields):
    name: str
    aliases: list[str] = Field(default_factory=list, max_length=100)
    category: str
    cuisine: str
    meal_type: str
    servings: FixtureServings
    ingredients: list[FixtureLine] = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=1, max_length=200)
    declared_allergen_ids: list[str] | None = None


class RecipeFixture(FixtureFields):
    format_version: Literal['recipe-fixture-v1']
    source: FixtureSource
    recipe: FixtureRecipe


def _unique_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate fixture key')
        result[key] = value
    return result


def _parse_fixture(text):
    try:
        payload = text.encode('utf-8')
    except UnicodeError:
        fail('INVALID_INPUT', '來源檔必須是 UTF-8。', 422)
    if len(payload) > MAX_FIXTURE_BYTES:
        fail('INVALID_INPUT', '食譜測試來源最多 256 KiB。', 413)
    try:
        fixture = RecipeFixture.model_validate(json.loads(payload, object_pairs_hook=_unique_keys))
    except (ValueError, RecursionError):
        fail('INVALID_INPUT', '食譜測試來源不符合 recipe-fixture-v1。', 422)
    return payload, fixture


def _text(value):
    result = unicodedata.normalize('NFC', value.strip())
    if not result or any(unicodedata.category(ch) in ('Cs', 'Cc') and ch not in '\t\n\r' for ch in result):
        fail('INVALID_INPUT', '食譜文字缺漏或含控制字元。', 422)
    return result


def _decimal(value):
    if value is None:
        return None
    value = value.strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        fail('INVALID_INPUT', '份量與重量需為非負十進位字串。', 422)
    integer, _, fraction = value.partition('.')
    integer = integer.lstrip('0') or '0'
    fraction = fraction.rstrip('0')
    return f'{integer}.{fraction}' if fraction else integer


def _measurement(value, artifact_id):
    decimal = _decimal(value)
    return {
        'value': decimal,
        'status': 'unknown' if decimal is None else 'declared',
        'evidence_ids': [artifact_id],
        'unknown_reason': '來源未提供數值' if decimal is None else None,
    }


def _recipe_content(recipe, artifact):
    artifact_id = artifact['artifact_id']
    name = _text(recipe.name)
    aliases = list(dict.fromkeys(_text(alias) for alias in recipe.aliases))
    lines = []
    for index, line in enumerate(recipe.ingredients, start=1):
        if line.ingredient_version_id is not None and line.food_id is None:
            fail('INVALID_INPUT', '指定食材版本時需要 Food ID。', 422)
        lines.append({
            'line_id': f'line-{index}',
            'food_id': line.food_id,
            'ingredient_version_id': line.ingredient_version_id,
            'canonical_name': _text(line.canonical_name),
            'raw_text': _text(line.raw_text),
            'amount': _measurement(line.amount, artifact_id),
            'unit': _text(line.unit) if line.unit is not None else None,
            'weight_g': _measurement(line.weight_g, artifact_id),
            'weight_basis': 'whole_source_recipe_before_cooking',
        })
    return {
        'logical_recipe_id': None,
        'standard_dish_id': None,
        'name': name,
        'aliases': [alias for alias in aliases if alias != name],
        'category': _text(recipe.category),
        'cuisine': _text(recipe.cuisine),
        'meal_type': _text(recipe.meal_type),
        'servings': {
            'count': _decimal(recipe.servings.count),
            'basis': recipe.servings.basis,
            'raw_text': _text(recipe.servings.raw_text),
            'portions_per_batch': _decimal(recipe.servings.portions_per_batch),
            'evidence_ids': [artifact_id],
        },
        'ingredients': lines,
        'steps': [{'step_no': number, 'instruction': _text(step)} for number, step in enumerate(recipe.steps, start=1)],
        'sources': [{
            'artifact_id': artifact_id,
            'artifact_sha256': artifact['sha256'],
            'origin_kind': 'supplied_document',
            'source_policy_version_id': None,
        }],
        'declared_allergen_ids': recipe.declared_allergen_ids,
    }


def _comparison_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _duplicate_suggestions(db, content, pins, source_hash):
    name = _comparison_name(content['name'])
    names = {name, *map(_comparison_name, content['aliases'])}
    suggestions = []

    def compare(existing, ref):
        current_name = _comparison_name(existing['name'])
        existing_names = {current_name, *map(_comparison_name, existing['aliases'])}
        reasons = []
        if any(source['artifact_sha256'] == source_hash for source in existing.get('sources', [])):
            reasons.append('EXACT_SOURCE_SHA256')
        if name == current_name:
            reasons.append('NORMALIZED_NAME')
        elif (names & existing_names) - {''}:
            reasons.append('DECLARED_ALIAS')
        elif min(len(name), len(current_name)) >= 4:
            similarity = SequenceMatcher(None, name, current_name, autojunk=False).ratio()
            if similarity >= 0.85:
                reasons.append('SIMILAR_NAME')
        if reasons:
            suggestions.append({**ref, 'name': existing['name'], 'reasons': reasons})

    if release := pins['legacy_recipe_release_id']:
        for recipe_id, recipe_name, aliases in db.execute(sa.select(
            Recipe.recipe_id, Recipe.recipe_name, Recipe.name_aliases,
        ).where(Recipe.recipe_release_id == release)):
            compare({'name': recipe_name, 'aliases': [alias for alias in aliases if isinstance(alias, str)]},
                    {'kind': 'legacy_recipe', 'recipe_id': recipe_id, 'release_id': release})

    if publication := pins['base_publication_id']:
        members, versions = T['publication_members'], T['entity_versions']
        for recipe_id, version_id, version_content in db.execute(sa.select(
            members.c.logical_id, versions.c.id, versions.c.content,
        ).join(versions, members.c.version_id == versions.c.id).where(members.c.publication_id == publication)):
            compare(version_content, {'kind': 'published_recipe', 'recipe_id': recipe_id,
                                      'publication_id': publication, 'version_id': version_id})

    proposals, revisions = T['proposals'], T['proposal_revisions']
    for pid, logical_id, revision, state, revision_content, content_hash in db.execute(sa.select(
        proposals.c.id, proposals.c.logical_id, proposals.c.current_revision,
        proposals.c.state, revisions.c.content, revisions.c.content_hash,
    ).join(revisions, sa.and_(revisions.c.proposal_id == proposals.c.id,
                             revisions.c.revision == proposals.c.current_revision)).where(
        proposals.c.domain == 'recipe', proposals.c.state.not_in(('published', 'withdrawn')),
    )):
        compare(revision_content, {'kind': 'proposal', 'recipe_id': logical_id, 'proposal_id': pid,
                                   'revision': revision, 'status': state, 'content_hash': content_hash})

    rank = {'EXACT_SOURCE_SHA256': 0, 'NORMALIZED_NAME': 1, 'DECLARED_ALIAS': 2, 'SIMILAR_NAME': 3}
    suggestions.sort(key=lambda item: (rank[item['reasons'][0]], item['kind'], item['recipe_id'],
                                       item.get('proposal_id', '')))
    return suggestions[:MAX_SUGGESTIONS], len(suggestions) > MAX_SUGGESTIONS


def import_fixture(service: Management, key, body):
    if (not isinstance(body, dict) or set(body) != {'fixture_json', 'dependency_pins', 'reason'}
            or not isinstance(body['fixture_json'], str)):
        fail('INVALID_INPUT', '需要食譜測試來源、依賴版本與操作原因。', 422)
    if not isinstance(body['reason'], str) or len(body['reason']) > 2000:
        fail('INVALID_INPUT', '需要有效的操作原因。', 422)
    reason = _text(body['reason'])
    payload, fixture = _parse_fixture(body['fixture_json'])
    pins = validate('DependencyPins', body['dependency_pins'])
    sha256 = hashlib.sha256(payload).hexdigest()
    source = {name: _text(value) if isinstance(value, str) else value
              for name, value in fixture.source.model_dump(exclude_none=True).items()}
    if 'ai_metadata' in source:
        source['ai_metadata'] = {name: _text(value) for name, value in source['ai_metadata'].items()}
    def action(command_id):
        service.require('recipe.read', 'recipe')
        if service.dependency_issues(pins, 'recipe'):
            fail('DEPENDENCY_CHANGED', '食譜來源所指定的依賴版本已變更。')
        child_key = f'recipe-fixture-{command_id}'
        artifact = service.upload(child_key, 'recipe', 'supplied_document', 'application/json', payload, reason)
        content = _recipe_content(fixture.recipe, artifact)
        proposal_body = validate('ProposalCreate', {'domain': 'recipe', 'content': content,
                                                  'dependency_pins': pins, 'reason': reason})
        suggestions, truncated = _duplicate_suggestions(service.db, proposal_body['content'], pins, sha256)
        proposal = service.create(child_key, proposal_body)
        receipt = {'proposal': proposal, 'artifact': artifact, 'source': source,
                   'duplicate_suggestions': suggestions, 'duplicate_suggestions_truncated': truncated}
        return receipt, proposal['proposal_id'], None, proposal['content_hash']

    request = {'fixture_sha256': sha256, 'dependency_pins': pins, 'reason': reason}
    return service.command(IMPORT_ACTION, 'recipe', key, request, 'recipe.edit', 'recipe', action)


def get_fixture(service: Management, proposal_id):
    service.require('recipe.read', 'recipe')
    service.require('artifact.read', 'recipe')
    audit, commands = T['audit_events'], T['commands']
    receipt = service.db.scalar(sa.select(commands.c.receipt).join(audit, audit.c.command_id == commands.c.id).where(
        audit.c.resource_id == proposal_id, audit.c.domain == 'recipe', audit.c.action == IMPORT_ACTION,
    ))
    if receipt is None:
        fail('NOT_FOUND', status=404)
    artifacts = T['artifacts']
    payload = service.db.scalar(sa.select(artifacts.c.payload).where(
        artifacts.c.id == receipt['artifact']['artifact_id'], artifacts.c.domain == 'recipe',
    ))
    if payload is None or hashlib.sha256(payload).hexdigest() != receipt['artifact']['sha256']:
        fail('SOURCE_EVIDENCE_REQUIRED')
    return {**receipt, 'fixture_json': bytes(payload).decode('utf-8')}
