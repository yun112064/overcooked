"""Read the immutable price publication selected by the T13 binding."""
from collections import defaultdict

import sqlalchemy as sa

from app.models.recipe import RecipeDataRelease
from app.source_adapters.price_scope import SCOPED_QUOTES_SQL, materialize_quote

from .contracts import digest
from .models import tables as T
from .price_policy import CONDITIONS, ManagedPrice, PricePreview, preview_price, source_freshness, window


PRICE_CHANNEL = 'price_policy'
LOCKED_QUOTES = sa.text(SCOPED_QUOTES_SQL + """
    SELECT DISTINCT ON (food_id, provider, product_id, product_name) *
    FROM scoped_quotes
    WHERE (food_id, provider, product_id, product_name) IN :keys AND queried_at <= :as_of
    ORDER BY food_id, provider, product_id, product_name, queried_at DESC, id DESC
""").bindparams(sa.bindparam('keys', expanding=True))


def active_recipe_release(db):
    return db.scalar(sa.select(RecipeDataRelease.release_id).where(RecipeDataRelease.active))


def price_readiness(entries, moment):
    """Evaluate current windows per food; retain unavailable foods as explicit warnings."""
    warnings = []
    usable = False
    for food_id, versions in entries.items():
        active = []
        expired = False
        for entry in versions:
            start, end = window(entry.content, entry.freshness)
            if (start is None or start <= moment) and (end is None or moment < end):
                active.append(entry)
            elif end is not None and moment >= end:
                expired = True
        reason = None
        if not active:
            reason = 'PRICE_EXPIRED' if expired else 'PRICE_NOT_YET_VALID'
        elif any(entry.content[field] is not None for entry in active for field in CONDITIONS):
            reason = 'PRICE_CONTEXT_REQUIRED'
        elif len({entry.content['provider'] for entry in active}) > 1:
            reason = 'PRICE_LOCKED_PROVIDER_REQUIRED'
        if reason:
            warnings.append(dict(code=reason, severity='warning', json_pointer='/foods/' + food_id,
                                 message=reason + ' · ' + food_id))
        else:
            usable = True
    if len(warnings) > 999:
        count = len(warnings)
        warnings = warnings[:998] + [dict(
            code='PRICE_READINESS_WARNINGS_TRUNCATED', severity='warning', json_pointer='',
            message='部分警告未展開 · 共 ' + str(count) + ' 項食材需確認',
        )]
    if not usable:
        return warnings[0]['code'] if warnings else 'PRICE_POLICY_INVALID', warnings[1:]
    return None, warnings


def published_entries(db, publication, food_ids=None):
    """Load exact review receipts, including approvals inherited from a base manifest."""
    publications = T['publications']
    members, versions, decisions = T['publication_members'], T['entity_versions'], T['review_decisions']
    rows = db.execute(sa.select(versions).join(members, versions.c.id == members.c.version_id).where(
        members.c.publication_id == publication['id'],
        versions.c.domain == 'price',
        sa.true() if food_ids is None else versions.c.content['food_id'].as_string().in_(food_ids),
    )).mappings().all()
    if food_ids is None and len(rows) != len(publication['manifest']['members']):
        return None
    if not rows:
        return {}

    lineage = sa.select(publications).where(publications.c.id == publication['id']).cte(recursive=True)
    lineage = lineage.union_all(sa.select(publications).join(lineage, publications.c.id == lineage.c.base_publication_id))
    review_ids = {review_id for manifest in db.scalars(sa.select(lineage.c.manifest))
                  for review_id in manifest['review_decision_ids']}
    reviews = defaultdict(dict)
    for decision in db.execute(sa.select(decisions).where(
        decisions.c.id.in_(review_ids), decisions.c.verdict == 'approve',
        decisions.c.proposal_id.in_({row['proposal_id'] for row in rows}),
    )).mappings():
        reviews[(decision['proposal_id'], decision['revision'], decision['content_hash'],
                 decision['dependency_hash'])][decision['scope']] = decision

    entries = defaultdict(list)
    pins = publication['manifest']['dependency_pins']
    freshness_by_provider = {}
    for row in rows:
        content = row['content']
        if digest('content', {'domain': 'price', 'content': content}) != row['content_hash']:
            return None
        scopes = reviews[(row['proposal_id'], row['revision'], row['content_hash'], row['dependency_hash'])]
        if set(scopes) != {'content', 'source_rights'} or scopes['content']['validation_id'] != scopes['source_rights']['validation_id']:
            return None
        provider = content['provider']
        if provider not in freshness_by_provider:
            freshness_by_provider[provider] = source_freshness(db, content, pins)
        entries[content['food_id']].append(ManagedPrice(row['id'], scopes['content']['id'], content,
                                                        freshness_by_provider[provider]))
    return entries


def active_overrides(db, food_ids, fallback, *, as_of, recipe_release_id=None):
    """Return decisions only for managed foods, unless the active scope is invalid."""
    if not food_ids:
        return {}
    bindings, publications = T['active_bindings'], T['publications']
    publication = db.execute(sa.select(publications).join(bindings, bindings.c.publication_id == publications.c.id)
        .where(bindings.c.channel == PRICE_CHANNEL)).mappings().one_or_none()
    if publication is None:
        return {}
    release_id = active_recipe_release(db)
    pins = publication['manifest']['dependency_pins']
    if release_id != pins['legacy_recipe_release_id'] or recipe_release_id not in (None, release_id):
        return dict.fromkeys(food_ids, PricePreview(None, 'PRICE_POLICY_RELEASE_MISMATCH'))
    if publication['domain'] != 'price' or digest('manifest', publication['manifest']) != publication['manifest_hash']:
        return dict.fromkeys(food_ids, PricePreview(None, 'PRICE_POLICY_INVALID'))
    entries = published_entries(db, publication, food_ids)
    if entries is None:
        return dict.fromkeys(food_ids, PricePreview(None, 'PRICE_POLICY_INVALID'))

    keys = sorted({(food, entry.content['provider'], entry.content['product_id'], entry.content['product_name'])
                   for food, versions in entries.items() for entry in versions
                   if entry.content['correction_kind'] == 'product_binding'})
    observations = defaultdict(list)
    if keys:
        for row in db.execute(LOCKED_QUOTES, {'keys': keys, 'as_of': as_of}).mappings():
            observations[row['food_id']].append((row['provider'], materialize_quote(row)))
    selected = {}
    for food, versions in entries.items():
        decision = preview_price(
            versions, observations[food], fallback.get(food), food_id=food, release_id=release_id,
            dictionary_id=pins['ingredient_publication_id'],
            ingredient_version_id=versions[0].content['ingredient_version_id'], as_of=as_of,
        )
        if decision.quote is not None:
            decision = PricePreview(decision.quote.model_copy(update={'price_policy_publication_id': publication['id']}), None)
        selected[food] = decision
    return selected
