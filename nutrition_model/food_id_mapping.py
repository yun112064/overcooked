"""Conservative FOOD→TFDA mappings under the user's 2026-10-30 policy.

Exact names, existing explicit T04 mappings, and unique same-state official
averages are usable. Alias-only matches remain candidates, not silent guesses.
"""
from collections.abc import Mapping, Sequence
from pathlib import Path
import hashlib
import json
import re
import unicodedata

POLICY_VERSION = 'exact-name-same-state-official-average-2026-10-30-v1'
AMBIGUOUS_NAMES = frozenset({'油', '絞肉', '肉', '肉末', '肉片', '肉絲', '魚', '魚肉', '魚片',
                             '酒', '蔬菜', '青菜', '高湯', '醬', '調味料', '香料'})


def _name(value):
    # No substring/fuzzy matching or removal of food-form words.
    return unicodedata.normalize('NFKC', str(value)).strip()


def _catalog(path):
    # Git may check out CRLF on Windows and LF on Linux. Pin the JSON content,
    # not the platform-specific newline bytes.
    raw = Path(path).read_bytes().replace(b'\r\n', b'\n')
    data = json.loads(raw)
    rows = {}
    for item in data:
        sid = item['ingredient_id']
        if sid in rows:
            raise ValueError('Duplicate nutrition sample ID')
        if (item.get('source', {}).get('provider') == 'TFDA'
                and item['source'].get('source_id') == sid
                and item['source'].get('source_version')):
            rows[sid] = item
    return rows, hashlib.sha256(raw).hexdigest()


def build_food_id_mappings(foods: Sequence[Mapping], catalog_path, *, manual_mappings=None,
                            source_manifest=None) -> dict:
    catalog, digest = _catalog(catalog_path)
    manual_mappings = manual_mappings or {}
    records = []
    seen = set()
    for food in foods:
        fid = food['food_id']
        names = sorted({_name(n) for n in food['names'] if isinstance(n, str) and n.strip()})
        if not isinstance(fid, str) or not re.fullmatch(r'FOOD\d{6}', fid) or fid in seen or not names:
            raise ValueError('Distinct FOOD IDs and canonical names required')
        seen.add(fid)
        record = dict(food_id=fid, canonical_names=names, status='pending', sample_id=None,
                      sample_state=None, match_rule=None, reason=None, candidate_ids=[])
        choices = []
        for name in names:
            if name in AMBIGUOUS_NAMES:
                choices.append((None, None, 'food_kind_unspecified', []))
                continue
            explicit = manual_mappings.get(name, {}).get('ingredient_id')
            if explicit:
                if explicit in catalog:
                    choices.append((explicit, 'existing_t04_explicit_mapping', None, [explicit]))
                else:
                    choices.append((None, None, 'explicit_mapping_sample_missing', [explicit]))
                continue
            exact = [r for r in catalog.values() if _name(r['standard_name']) == name]
            averages = [r for r in catalog.values() if _name(r['standard_name']) == name + '平均值']
            candidates = exact + averages
            states = {r.get('state') for r in candidates}
            if len(states) > 1:
                choices.append((None, None, 'sample_state_ambiguous', [r['ingredient_id'] for r in candidates]))
            elif len(averages) == 1 and averages[0].get('state') in ('raw', 'cooked'):
                choices.append((averages[0]['ingredient_id'], 'unique_same_state_official_average', None,
                                [r['ingredient_id'] for r in candidates]))
            elif len(exact) == 1 and not averages:
                choices.append((exact[0]['ingredient_id'], 'unique_exact_standard_name', None,
                                [exact[0]['ingredient_id']]))
            elif candidates:
                choices.append((None, None, 'sample_or_average_ambiguous', [r['ingredient_id'] for r in candidates]))
            else:
                aliases = [r['ingredient_id'] for r in catalog.values()
                           if any(_name(alias) == name for alias in r.get('aliases', []))]
                choices.append((None, None, 'alias_requires_confirmation' if aliases else 'tfda_sample_not_found', aliases))
        record['candidate_ids'] = sorted({sid for _, _, _, ids in choices for sid in ids})
        chosen = {sid for sid, _, _, _ in choices if sid is not None}
        if len(chosen) == 1 and all(sid is not None for sid, _, _, _ in choices):
            sid = next(iter(chosen))
            sample = catalog[sid]
            record.update(status='approved', sample_id=sid, sample_state=sample.get('state'),
                          match_rule=choices[0][1], source=sample['source'],
                          approval_basis='User-approved deterministic mapping policy; not a claim of per-record human review')
        else:
            record['reason'] = ('canonical_name_conflict' if len(chosen) > 1 else
                                next((reason for _, _, reason, _ in choices if reason), 'mapping_ambiguous'))
        records.append(record)
    return dict(policy_version=POLICY_VERSION, catalog_sha256=digest,
                source_manifest=dict(source_manifest or {}), records=records,
                counts=dict(approved=sum(r['status'] == 'approved' for r in records),
                            pending=sum(r['status'] != 'approved' for r in records)))


def load_food_id_mappings(path, catalog_path, *, expected_names=None) -> dict[str, str]:
    """Load only approved bindings, verifying the pinned TFDA snapshot."""
    catalog, digest = _catalog(catalog_path)
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if document.get('policy_version') != POLICY_VERSION or document.get('catalog_sha256') != digest:
        raise ValueError('Food mapping policy/catalog snapshot mismatch; regenerate mappings')
    mappings = {}
    seen = set()
    for record in document['records']:
        fid = record['food_id']
        if not isinstance(fid, str) or not re.fullmatch(r'FOOD\d{6}', fid) or fid in seen:
            raise ValueError('Invalid/duplicate FOOD ID')
        seen.add(fid)
        if record['status'] != 'approved':
            continue
        if expected_names is not None and fid in expected_names:
            allowed = {_name(n) for n in record.get('canonical_names', [])}
            if not expected_names[fid] or any(not isinstance(n, str) or _name(n) not in allowed
                                              for n in expected_names[fid]):
                raise ValueError('Recipe FOOD ID/name differs from the approved mapping')
        sid = record['sample_id']
        sample = catalog.get(sid)
        if (not sample or sample.get('state') != record.get('sample_state')
                or sample['source'] != record.get('source')):
            raise ValueError('Approved FOOD mapping no longer matches its TFDA sample')
        mappings[fid] = sid
    return mappings
