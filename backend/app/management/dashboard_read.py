"""Read-only management views. Writes remain in the shared command service."""
import base64
import binascii
import json
from datetime import datetime
from itertools import islice

import sqlalchemy as sa

from app.api.errors import ApiError
from app.management.contracts import POLICY, ROOT, VERSION, digest, rules_pin
from app.management.models import tables as T
from app.management.service import fail, legacy_capture, now, stamp
from app.models.recipe import IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient, RecipeStep

DATA_TYPES = json.loads((ROOT / 'data_types.json').read_text(encoding='utf-8'))
CHANNEL_DOMAINS = {'recipe_catalog': 'recipe', 'ingredient_catalog': 'ingredient',
                   'price_policy': 'price', 'source_policy': 'source_policy'}
CONNECTION_TARGETS = {
    'ai-fixture': 'recipe', 'manual-fixture': 'source_policy',
    'pxmart-fixture': 'price', 'uniprosperity-fixture': 'price',
}


def encode_cursor(scope, keys):
    raw = json.dumps([digest('dashboard-cursor', scope), *keys], separators=(',', ':'))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def decode_cursor(cursor, scope, kinds):
    if cursor is None:
        return None
    try:
        raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
        checksum, *keys = json.loads(raw)
        if (checksum != digest('dashboard-cursor', scope) or len(keys) != len(kinds)
                or any(type(value) is not kind for value, kind in zip(keys, kinds, strict=True))):
            raise ValueError
        return keys
    except (binascii.Error, UnicodeError, ValueError, TypeError):
        fail('INVALID_CURSOR', '這個分頁連結已失效，請回到第一頁。', 422)


def page(items, limit, scope, key, project=lambda row: dict(row)):
    return {
        'items': [project(row) for row in items[:limit]],
        'next_cursor': encode_cursor(scope, key(items[limit - 1])) if len(items) > limit else None,
    }


def dated_page(s, query, table, limit, cursor, scope, project):
    keys = decode_cursor(cursor, scope, (str, str))
    if keys:
        try:
            created_at = datetime.fromisoformat(keys[0])
            if created_at.tzinfo is None:
                raise ValueError
        except ValueError:
            fail('INVALID_CURSOR', '這個分頁連結已失效，請回到第一頁。', 422)
        query = query.where(sa.or_(table.c.created_at < created_at,
                                   sa.and_(table.c.created_at == created_at, table.c.id < keys[1])))
    rows = s.db.execute(query.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)).mappings().all()
    return page(rows, limit, scope, lambda row: (stamp(row['created_at']), row['id']), project)


def visible_domains(s):
    return sorted({domain for grant in s.grants() for domain in grant['domain_scope']
                   if domain + '.read' in POLICY['role_presets'][grant['role']]})


def proposal_view(p, r):
    return dict(proposal_id=p['id'], domain=p['domain'], revision=r['revision'], status=p['state'],
                content=r['content'], dependency_pins=r['dependency_pins'], content_hash=r['content_hash'],
                dependency_hash=r['dependency_hash'], created_by=p['created_by'], contributors=p['contributor_ids'],
                created_at=stamp(p['created_at']), updated_at=stamp(p['updated_at']))


def visible_proposal(s, proposal_id):
    p = s.proposal(proposal_id)
    try:
        s.require(p['domain'] + '.read', p['domain'])
    except ApiError:
        fail('NOT_FOUND', status=404)
    return p


def proposals(s, domain, state, limit, cursor):
    s.require(domain + '.read', domain)
    p, r = T['proposals'], T['proposal_revisions']
    query = sa.select(p, r.c.revision, r.c.content, r.c.dependency_pins, r.c.content_hash, r.c.dependency_hash).join(
        r, sa.and_(r.c.proposal_id == p.c.id, r.c.revision == p.c.current_revision)
    ).where(p.c.domain == domain)
    if state is not None:
        query = query.where(p.c.state == state)
    return dated_page(s, query, p, limit, cursor, ['proposals', domain, state], lambda row: proposal_view(row, row))


def revision(s, p, number):
    t = T['proposal_revisions']
    row = s.row('proposal_revisions', sa.and_(t.c.proposal_id == p['id'], t.c.revision == number))
    if row is None:
        fail('NOT_FOUND', status=404)
    return row


def walk_changes(before, after, pointer=''):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in before:
                yield dict(op='add', json_pointer=path, before=None, after=after[key])
            elif key not in after:
                yield dict(op='remove', json_pointer=path, before=before[key], after=None)
            else:
                yield from walk_changes(before[key], after[key], path)
    elif isinstance(before, list) and isinstance(after, list):
        for index, (old, new) in enumerate(zip(before, after)):
            yield from walk_changes(old, new, f'{pointer}/{index}')
        for index in range(min(len(before), len(after)), max(len(before), len(after))):
            if index >= len(before):
                yield dict(op='add', json_pointer=f'{pointer}/{index}', before=None, after=after[index])
            else:
                yield dict(op='remove', json_pointer=f'{pointer}/{index}', before=before[index], after=None)
    elif before != after:
        yield dict(op='replace', json_pointer=pointer, before=before, after=after)


def proposal_diff(s, p, base, target, limit, cursor):
    def value(number):
        row = revision(s, p, number)
        return {'content': row['content'], 'dependency_pins': row['dependency_pins']}

    scope = ['proposal_diff', p['id'], base, target]
    keys = decode_cursor(cursor, scope, (int,))
    offset = keys[0] if keys else 0
    if not 0 <= offset <= 100_000:
        fail('INVALID_CURSOR', status=422)
    changes = list(islice(walk_changes(value(base), value(target)), offset, offset + limit + 1))
    return dict(proposal_id=p['id'], base_revision=base, target_revision=target, changes=changes[:limit],
                next_cursor=encode_cursor(scope, (offset + limit,)) if len(changes) > limit else None)


def proposal_workflow(s, p):
    r = s.revision(p)
    v = T['validation_runs']
    validation = s.db.execute(sa.select(v).where(
        v.c.proposal_id == p['id'], v.c.revision == r['revision'],
        v.c.content_hash == r['content_hash'], v.c.dependency_hash == r['dependency_hash']
    ).order_by(v.c.created_at.desc(), v.c.id.desc()).limit(1)).mappings().first()
    approvals = {}
    result = None
    if validation is not None:
        result = dict(validation_id=validation['id'], evaluated_at=stamp(validation['created_at']),
                      catalog_ready=validation['catalog_ready'], issues=validation['issues'])
        approvals = {scope: review['id'] for scope, review in s.latest_approvals(p, r).items()
                     if review['validation_id'] == validation['id']}
    t = T['tasks']
    newest_task = s.db.execute(sa.select(t).where(
        t.c.params['proposal_id'].as_string() == p['id'],
        t.c.params['revision'].as_integer() == r['revision'],
    ).order_by(t.c.created_at.desc(), t.c.id.desc()).limit(1)).mappings().first()
    return dict(revision=r['revision'], content_hash=r['content_hash'], validation=result, approvals=approvals,
                latest_task=task_view(newest_task) if newest_task is not None else None)


def publication_view(row):
    return dict(publication_id=row['id'], domain=row['domain'], manifest_hash=row['manifest_hash'],
                base_publication_id=row['base_publication_id'], member_count=len(row['manifest']['members']),
                created_at=stamp(row['created_at']), created_by=row['created_by'], formal_generation_ready=False)


def visible_publication(s, publication_id, domain):
    t = T['publications']
    row = s.row('publications', sa.and_(t.c.id == publication_id, t.c.domain == domain))
    if row is None:
        fail('NOT_FOUND', status=404)
    return row


def publications(s, domain, limit, cursor, legacy_recipe_release_id=None, ingredient_publication_id=None):
    s.require(domain + '.read', domain)
    t = T['publications']
    query = sa.select(t).where(t.c.domain == domain)
    scope = ['publications', domain]
    if legacy_recipe_release_id:
        query = query.where(t.c.manifest['dependency_pins']['legacy_recipe_release_id'].as_string() == legacy_recipe_release_id)
        scope.append(legacy_recipe_release_id)
    if ingredient_publication_id:
        query = query.where(t.c.manifest['dependency_pins']['ingredient_publication_id'].as_string() == ingredient_publication_id)
        scope.extend(['ingredient', ingredient_publication_id])
    return dated_page(s, query, t, limit, cursor, scope, publication_view)


def binding(s, channel):
    domain = CHANNEL_DOMAINS[channel]
    s.require(domain + '.read', domain)
    t = T['active_bindings']
    row = s.row('active_bindings', t.c.channel == channel)
    if row is None:
        fail('MANAGEMENT_SCHEMA_REQUIRED', status=503)
    return dict(channel=channel, publication_id=row['publication_id'], revision=row['revision'], updated_at=stamp(row['updated_at']))


def draft_context(s, domain, base_publication_id, ingredient_publication_id, legacy_recipe_release_id, proposal_id):
    s.require(domain + '.edit', domain)
    pins = dict.fromkeys([
        'legacy_recipe_release_id', 'legacy_recipe_source_sha256', 'legacy_db_capture_sha256',
        'base_publication_id', 'base_publication_manifest_sha256', 'ingredient_publication_id',
        'ingredient_manifest_sha256', 'source_policy_publication_id', 'source_policy_manifest_sha256',
    ])
    if proposal_id:
        proposal = visible_proposal(s, proposal_id)
        if domain != proposal['domain'] or base_publication_id:
            fail('INVALID_INPUT', status=422)
        pins.update(s.revision(proposal)['dependency_pins'])
    if base_publication_id:
        publication = visible_publication(s, base_publication_id, domain)
        pins.update(publication['manifest']['dependency_pins'])
        pins.update(base_publication_id=publication['id'], base_publication_manifest_sha256=publication['manifest_hash'])
        if domain == 'price' and (
            ingredient_publication_id not in (None, pins['ingredient_publication_id'])
            or legacy_recipe_release_id not in (None, pins['legacy_recipe_release_id'])
        ):
            fail('DEPENDENCY_CHANGED')
    if ingredient_publication_id:
        s.require('ingredient.read', 'ingredient')
        ingredient = visible_publication(s, ingredient_publication_id, 'ingredient')
        pins.update(ingredient_publication_id=ingredient['id'], ingredient_manifest_sha256=ingredient['manifest_hash'])
    if legacy_recipe_release_id:
        if domain not in ('recipe', 'ingredient', 'price'):
            fail('INVALID_INPUT', status=422)
        release = s.db.get(RecipeDataRelease, legacy_recipe_release_id)
        if release is None:
            fail('NOT_FOUND', status=404)
        pins.update(legacy_recipe_release_id=release.release_id, legacy_recipe_source_sha256=release.source_sha256,
                    legacy_db_capture_sha256=legacy_capture(s.db))
    return dict(pins, **rules_pin(), contract_version=VERSION)


def task_view(row, observed_at=None):
    return dict(task_id=row['id'], kind=row['kind'], revision=row['revision'], status=row['state'],
                attempt=row['attempt'], observed_at=observed_at or stamp(now()), execution_mode='fixtures_only')


def scoped_tasks(domains):
    t, p = T['tasks'], T['proposals']
    return sa.select(t).join(p, t.c.params['proposal_id'].as_string() == p.c.id).where(p.c.domain.in_(domains))


def tasks(s, limit, cursor):
    s.require('operations.read')
    domains = visible_domains(s)
    observed_at = stamp(now())
    return dated_page(s, scoped_tasks(domains), T['tasks'], limit, cursor, ['tasks', domains],
                      lambda row: task_view(row, observed_at))


def connections(s):
    s.require('operations.read')
    domains = visible_domains(s)
    return dict(items=[
        dict(registered_target_key=key, status='not_configured', checked_at=None, latency_ms=None,
             reason_code='LIVE_PROBE_DISABLED')
        for key, domain in CONNECTION_TARGETS.items() if domain in domains
    ], next_cursor=None)


def metrics(s):
    s.require('operations.read')
    domains = visible_domains(s)
    p = T['proposals']
    pending = (s.db.scalar(sa.select(sa.func.count()).select_from(p).where(p.c.domain.in_(domains), p.c.state == 'submitted'))
               if domains else None)
    running = (s.db.scalar(sa.select(sa.func.count()).select_from(
        scoped_tasks(domains).where(T['tasks'].c.state == 'running').subquery())) if domains else None)
    result = dict(as_of=stamp(now()), data_origin='metadata_snapshot', recipe_count=None, pending_proposal_count=pending,
                  database_bytes=None, running_task_count=running)
    return dict(result, missing_metrics=[key for key, value in result.items() if value is None])


def entities(s, domain, view_id, logical_id=None, limit=50, cursor=None):
    s.require(domain + '.read', domain)
    visible_publication(s, view_id, domain)
    m, v = T['publication_members'], T['entity_versions']
    query = sa.select(m.c.logical_id, m.c.ref, v.c.content).join(v, m.c.version_id == v.c.id).where(
        m.c.publication_id == view_id, v.c.domain == domain,
    )
    scope = ['entities', domain, view_id]
    keys = decode_cursor(cursor, scope, (str,))
    if keys:
        query = query.where(m.c.logical_id > keys[0])
    if logical_id:
        query = query.where(m.c.logical_id == logical_id)
    rows = s.db.execute(query.order_by(m.c.logical_id).limit(limit + 1)).mappings().all()

    def project(row):
        return dict(domain=domain, logical_id=row['logical_id'], ref=row['ref'], content=row['content'],
                    approval_state='managed_approved')

    if logical_id:
        if not rows:
            fail('NOT_FOUND', status=404)
        return project(rows[0])
    return page(rows, limit, scope, lambda row: (row['logical_id'],), project)


def legacy_entities(s, domain, release_id, limit, cursor):
    """Legacy browse uses a distinct DTO: historical rows lack rc1 artifact references."""
    s.require(domain + '.read', domain)
    release = s.db.get(RecipeDataRelease, release_id)
    if not release:
        fail('NOT_FOUND', status=404)
    model, key, name = ((Recipe, Recipe.recipe_id, Recipe.recipe_name) if domain == 'recipe'
                        else (IngredientAlias, IngredientAlias.food_id, IngredientAlias.canonical_name))
    scope = ['legacy_entities', domain, release_id]
    keys = decode_cursor(cursor, scope, (str,))
    query = sa.select(key.label('logical_id'), name.label('name')).where(model.recipe_release_id == release_id)
    if keys:
        query = query.where(key > keys[0])
    rows = s.db.execute(query.order_by(key).limit(limit + 1)).mappings().all()
    return page(rows, limit, scope, lambda row: (row['logical_id'],),
                lambda row: dict(row, approval_state='legacy_unverified'))


def legacy_releases(s, domain, limit, cursor):
    s.require(domain + '.read', domain)
    t = RecipeDataRelease.__table__
    scope = ['legacy_releases', domain]
    keys = decode_cursor(cursor, scope, (str,))
    query = sa.select(t.c.release_id, t.c.dataset_version, t.c.active, t.c.imported_at)
    if keys:
        query = query.where(t.c.release_id > keys[0])
    rows = s.db.execute(query.order_by(t.c.release_id).limit(limit + 1)).mappings().all()
    return page(rows, limit, scope, lambda row: (row['release_id'],),
                lambda row: dict(row, imported_at=stamp(row['imported_at'])))


def decimal_string(value):
    if value is None:
        return None
    return format(value.normalize(), 'f')


def legacy_entity(s, domain, release_id, logical_id):
    s.require(domain + '.read', domain)
    model = Recipe if domain == 'recipe' else IngredientAlias
    entity = s.db.get(model, logical_id)
    if entity is None or entity.recipe_release_id != release_id:
        fail('NOT_FOUND', status=404)
    view = dict(approval_state='legacy_unverified', logical_id=logical_id)
    if domain == 'ingredient':
        return dict(view, name=entity.canonical_name, aliases=entity.aliases)
    line = RecipeIngredient
    step = RecipeStep
    ingredients = s.db.scalars(sa.select(line).where(line.recipe_id == logical_id, line.recipe_release_id == release_id)
                               .order_by(line.sequence).limit(201)).all()
    steps = s.db.scalars(sa.select(step).where(step.recipe_id == logical_id, step.recipe_release_id == release_id)
                         .order_by(step.step_no).limit(201)).all()
    if len(ingredients) > 200 or len(steps) > 200:
        fail('LEGACY_REVIEW_REQUIRED', '舊版食譜超出管理修訂的欄位上限。', 422)
    return dict(
        view, name=entity.recipe_name, aliases=entity.name_aliases, category=entity.category, cuisine=entity.cuisine,
        meal_type=entity.meal_type, standard_dish_id=entity.standard_dish_id, servings_basis=entity.servings_basis,
        servings_raw=entity.servings_raw, original_servings=decimal_string(entity.original_servings),
        ingredients=[
            dict(line_id=row.recipe_ingredient_id, food_id=row.food_id, canonical_name=row.canonical_name,
                 raw_text=row.raw_text, source_amount=decimal_string(row.source_amount), source_unit=row.source_unit,
                 source_weight_g=decimal_string(row.source_weight_g)) for row in ingredients
        ],
        steps=[dict(step_no=row.step_no, instruction=row.instruction_clean) for row in steps],
    )
