"""Atomic, hash-bound T02 history import. No retailer or model calls."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from .price_models import PriceQuote

SCHEMA = 't02-price-history-release-v1'
RUN_JSON_FIELDS = {'status_counts', 'missing_reasons'}
RUN_FIELDS = {'auto_adopted_count', 'checkpoint_count', 'duration_ms', 'estimate_count',
              'failure_count', 'fatal_error', 'finished_at', 'id', 'ingredient_total',
              'mapping_failure_rate', 'missing_count', 'missing_reasons',
              'price_update_success_rate', 'provider', 'review_count', 'run_key',
              'scheduled_for', 'started_at', 'status', 'status_counts', 'success_count', 'trigger_type'}
QUOTE_FIELDS = {'canonical_name', 'currency', 'missing_reason', 'package_quantity', 'package_unit',
                'price', 'product_id', 'product_name', 'provider', 'queried_at', 'quote_payload',
                'run_key', 'source_url', 'status', 'valid_from', 'valid_until'}
DECIMALS = {'price', 'package_quantity', 'mapping_failure_rate', 'price_update_success_rate'}
DATES = {'queried_at', 'valid_from', 'valid_until', 'scheduled_for', 'started_at', 'finished_at'}


@dataclass(frozen=True)
class PriceBundle:
    manifest: dict
    manifest_sha256: str
    runs: list[dict]
    quotes: list[dict]


def _name(value):
    return unicodedata.normalize('NFKC', value).strip()


def _value(field, value):
    if value is None:
        return None
    if field in DECIMALS:
        return Decimal(str(value))
    if field in DATES:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if parsed.utcoffset() is None:
            raise ValueError('Source timestamps must include timezone: ' + field)
        return parsed
    return value


def load_bundle(manifest_path: Path, expected_sha256: str) -> PriceBundle:
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Price manifest hash mismatch')
    manifest = json.loads(raw)
    if (manifest.get('schema_version') != SCHEMA
            or manifest.get('usage_status') != 'historical_requires_refresh'
            or manifest.get('selection_policy') != 'exact_food_id_and_canonical_name_v1'):
        raise ValueError('Unsupported or unsafe price source policy')
    identity = {k: v for k, v in manifest.items() if k != 'release_id'}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if manifest.get('release_id') != 'price-history-' + digest[:20]:
        raise ValueError('Price release identity does not match source manifest')
    if set(manifest['files']) != {'price_collection_runs.json', 'price_quote_snapshots.json'}:
        raise ValueError('Only T02 price sources may be imported')
    files = {}
    for name, info in manifest['files'].items():
        path = (manifest_path.parent / info['file']).resolve()
        if path.parent != manifest_path.parent.resolve():
            raise ValueError('Source file must be inside the release folder')
        compressed = path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != info['compressed_sha256']:
            raise ValueError('Compressed price source hash mismatch: ' + name)
        content = gzip.decompress(compressed)
        if len(content) != info['bytes'] or hashlib.sha256(content).hexdigest() != info['sha256']:
            raise ValueError('Original price source hash mismatch: ' + name)
        rows = json.loads(content)
        if not isinstance(rows, list) or len(rows) != info['count']:
            raise ValueError('Source row count mismatch: ' + name)
        files[name] = rows
    bundle = PriceBundle(manifest, expected_sha256, files['price_collection_runs.json'], files['price_quote_snapshots.json'])
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: PriceBundle):
    runs = {}
    ids = set()
    for run in bundle.runs:
        if set(run) != RUN_FIELDS or run['provider'] not in {'pxmart', 'uniprosperity'}:
            raise ValueError('Unexpected collection run fields or provider')
        if run['run_key'] in runs or run['id'] in ids:
            raise ValueError('Duplicate source run')
        runs[run['run_key']] = run
        ids.add(run['id'])
        for field, value in run.items():
            _value(field, value)
    seen = set()
    for row in bundle.quotes:
        if set(row) != QUOTE_FIELDS or row['run_key'] not in runs:
            raise ValueError('Unexpected quote fields or missing run')
        if row['provider'] != runs[row['run_key']]['provider']:
            raise ValueError('Quote provider differs from its run')
        key = (row['run_key'], row['canonical_name'])
        if key in seen:
            raise ValueError('Duplicate source snapshot')
        seen.add(key)
        quote = PriceQuote.model_validate(row['quote_payload'])
        for field in ('canonical_name', 'status', 'price', 'currency', 'package_quantity',
                      'package_unit', 'queried_at', 'valid_from', 'valid_until', 'source_url',
                      'missing_reason', 'product_id', 'product_name'):
            actual = getattr(quote, field)
            if _value(field, actual) != _value(field, row[field]):
                raise ValueError('Quote payload/scalar mismatch: ' + field)
        if not quote.food_ids or len(quote.food_ids) != len(set(quote.food_ids)):
            raise ValueError('Missing or duplicate source Food_IDs')
        if any(not re.fullmatch(r'FOOD\d{6}', fid) for fid in quote.food_ids):
            raise ValueError('Invalid source Food_ID')


def binding_plan(bundle: PriceBundle, catalog: dict[str, str]) -> tuple[dict, list[dict]]:
    bindings = {}
    unresolved = []
    for row in bundle.quotes:
        matched = []
        for food_id in row['quote_payload']['food_ids']:
            target = catalog.get(food_id)
            if target is not None and _name(target) == _name(row['canonical_name']):
                matched.append(food_id)
            else:
                unresolved.append({'run_key': row['run_key'], 'source_name': row['canonical_name'],
                    'food_id': food_id, 'v3_name': target,
                    'reason': 'food_id_not_in_release' if target is None else 'canonical_name_mismatch'})
        bindings[(row['run_key'], row['canonical_name'])] = matched
    return bindings, unresolved


def _assert_target(connection):
    name = connection.scalar(text('SELECT current_database()'))
    if name != 'meal_planner_1102_v3' and not re.fullmatch(r'meal_planner_1102_v3_t02_test(?:_[a-z0-9_]+)?', name):
        raise ValueError('T02 writes are restricted to dedicated V3 databases')
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('overcooked:t02:price-release', 0))"))


def _catalog(connection, release_id):
    active = connection.execute(text('SELECT release_id FROM recipe_data_releases WHERE active')).scalars().all()
    if active != [release_id]:
        raise ValueError('Source manifest must match the one active T01 recipe release')
    return dict(connection.execute(text(
        'SELECT food_id, canonical_name FROM ingredient_aliases WHERE recipe_release_id = :release'
    ), {'release': release_id}).all())


def _insert(connection, table, row, json_fields=(), returning=''):
    # table and columns come exclusively from the checked T02 schemas above.
    columns = sorted(row)
    placeholders = ['CAST(:' + c + ' AS jsonb)' if c in json_fields else ':' + c for c in columns]
    values = {c: json.dumps(row[c], ensure_ascii=False) if c in json_fields else _value(c, row[c]) for c in columns}
    return connection.execute(text('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES ('
                                   + ','.join(placeholders) + ')' + returning), values)


def verify_installed(connection, bundle, bindings):
    release = bundle.manifest['release_id']
    runs = connection.execute(text('SELECT * FROM price_collection_runs WHERE price_data_release_id = :release'),
                              {'release': release}).mappings().all()
    actual_runs = {r['run_key']: r for r in runs}
    if len(runs) != len(bundle.runs):
        raise ValueError('Stored collection run count differs')
    for source in bundle.runs:
        actual = actual_runs.get(source['run_key'])
        if actual is None or any(_value(c, actual[c]) != _value(c, v) for c, v in source.items()):
            raise ValueError('Stored collection run differs from source')
        if actual['recipe_release_id'] != bundle.manifest['recipe_release_id']:
            raise ValueError('Stored run recipe release differs')
    snapshots = connection.execute(text(
        'SELECT s.*, r.run_key FROM price_quote_snapshots s JOIN price_collection_runs r ON r.id = s.run_id '
        'WHERE r.price_data_release_id = :release'), {'release': release}).mappings().all()
    actual_quotes = {(q['run_key'], q['canonical_name']): q for q in snapshots}
    if len(snapshots) != len(bundle.quotes):
        raise ValueError('Stored snapshot count differs')
    links = connection.execute(text(
        'SELECT f.*, r.run_key, s.canonical_name FROM price_snapshot_foods f '
        'JOIN price_quote_snapshots s ON s.id = f.snapshot_id JOIN price_collection_runs r ON r.id = s.run_id '
        'WHERE r.price_data_release_id = :release'), {'release': release}).mappings().all()
    stored_links = set()
    for f in links:
        if f['recipe_release_id'] != bundle.manifest['recipe_release_id'] or f['mapping_method'] != 'exact_food_id_and_name':
            raise ValueError('Stored food binding provenance differs')
        stored_links.add((f['run_key'], f['canonical_name'], f['food_id']))
    for source in bundle.quotes:
        actual = actual_quotes.get((source['run_key'], source['canonical_name']))
        if actual is None or any(_value(c, actual[c]) != _value(c, v) for c, v in source.items()):
            raise ValueError('Stored snapshot differs from source')
    expected = {(key[0], key[1], fid) for key, ids in bindings.items() for fid in ids}
    if expected != stored_links:
        raise ValueError('Stored food binding set differs')


def install(connection, bundle: PriceBundle) -> dict:
    _assert_target(connection)
    validate_bundle(bundle)
    manifest = bundle.manifest
    release_id, recipe_id = manifest['release_id'], manifest['recipe_release_id']
    catalog = _catalog(connection, recipe_id)
    bindings, unresolved = binding_plan(bundle, catalog)
    counts = {'price_collection_runs': len(bundle.runs), 'price_quote_snapshots': len(bundle.quotes),
              'price_snapshot_foods': sum(map(len, bindings.values()))}
    existing = connection.execute(text('SELECT * FROM price_data_releases WHERE release_id = :release'),
                                  {'release': release_id}).mappings().one_or_none()
    if existing:
        if (existing['manifest_sha256'] != bundle.manifest_sha256 or existing['source_manifest'] != manifest
                or existing['recipe_release_id'] != recipe_id or existing['usage_status'] != 'historical_requires_refresh'
                or not existing['active'] or existing['row_counts'] != counts):
            raise ValueError('Existing price release differs; refusing replacement')
        verify_installed(connection, bundle, bindings)
        status = 'unchanged'
    else:
        if connection.scalar(text('SELECT count(*) FROM price_data_releases WHERE recipe_release_id = :recipe AND active'),
                             {'recipe': recipe_id}):
            raise ValueError('Another price release is active; refusing implicit replacement')
        run_keys = [r['run_key'] for r in bundle.runs]
        if connection.scalar(text('SELECT count(*) FROM price_collection_runs WHERE run_key = ANY(:keys)'), {'keys': run_keys}):
            raise ValueError('Source run keys already exist outside this release')
        _insert(connection, 'price_data_releases', {
            'release_id': release_id, 'recipe_release_id': recipe_id, 'source_ref': manifest['source_ref'],
            'manifest_sha256': bundle.manifest_sha256, 'source_manifest': manifest, 'row_counts': counts,
            'usage_status': 'historical_requires_refresh', 'active': True,
        }, {'source_manifest', 'row_counts'})
        run_ids = {}
        for run in bundle.runs:
            _insert(connection, 'price_collection_runs', {**run, 'recipe_release_id': recipe_id,
                    'price_data_release_id': release_id}, RUN_JSON_FIELDS)
            run_ids[run['run_key']] = run['id']
        for quote in bundle.quotes:
            row = {k: v for k, v in quote.items() if k != 'run_key'}
            row['run_id'] = run_ids[quote['run_key']]
            snapshot_id = _insert(connection, 'price_quote_snapshots', row, {'quote_payload'}, ' RETURNING id').scalar_one()
            for food_id in bindings[(quote['run_key'], quote['canonical_name'])]:
                _insert(connection, 'price_snapshot_foods', {'snapshot_id': snapshot_id, 'recipe_release_id': recipe_id,
                        'food_id': food_id, 'mapping_method': 'exact_food_id_and_name'})
        verify_installed(connection, bundle, bindings)
        status = 'installed'
    return {'status': status, 'release_id': release_id, 'recipe_release_id': recipe_id,
            'manifest_sha256': bundle.manifest_sha256, 'usage_status': 'historical_requires_refresh',
            'counts': counts, 'bound_food_ids': len({fid for ids in bindings.values() for fid in ids}),
            'unresolved_bindings': unresolved, 'snapshot_status_counts': dict(Counter(q['status'] for q in bundle.quotes)),
            'provider_counts': dict(Counter(q['provider'] for q in bundle.quotes)),
            'new_retailer_product_mappings': 0}
