"""Ingredient nutrition knowledge contract (R12, G07). No production sources are bundled.

Knowledge text is kept separate from TFDA numeric nutrition: missing knowledge is an
omittable state, while missing numeric nutrition still blocks adoption elsewhere.
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlparse

FIELDS = ('knowledge_id', 'ingredient_id', 'excerpt', 'source_url', 'source_version', 'rights', 'conditions')


class KnowledgeStore:
    """Versioned records and immutable returned snapshots; caller persists JSON.

    Import always creates a pending version. review() is an administrative API;
    never expose it to untrusted recipe clients. Updating content creates a new
    pending version and removes the older one from retrieval, not from history.
    """
    def __init__(self, ingredient_ids, records=None):
        self.ingredient_ids = set(ingredient_ids)
        self.records = copy.deepcopy(records or [])
        for record in self.records:
            self._validate(record)

    def _validate(self, record):
        if not isinstance(record, dict):
            raise ValueError('Knowledge record must be an object')
        for key in FIELDS:
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f'Missing knowledge {key}')
        if record['ingredient_id'] not in self.ingredient_ids:
            raise ValueError('Unknown ingredient ID')
        parsed = urlparse(record['source_url'])
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Invalid source URL')

    def import_record(self, record):
        self._validate(record)
        if any(r['knowledge_id'] == record['knowledge_id'] and r['source_version'] == record['source_version']
               for r in self.records):
            raise ValueError('Existing versions are immutable; import a new source_version')
        clean = {k: record[k] for k in FIELDS}
        clean.update(status='pending', reviewer=None, reviewed_at=None, review_note=None)
        self.records.append(clean)
        return copy.deepcopy(clean)

    def review(self, knowledge_id, source_version, *, reviewer, reviewed_at, approved, note):
        if not all(isinstance(v, str) and v.strip() for v in (reviewer, reviewed_at, note)) or not isinstance(approved, bool):
            raise ValueError('Reviewer, review date, decision and citation-check note required')
        matches = [r for r in self.records if r['knowledge_id'] == knowledge_id and r['source_version'] == source_version]
        if len(matches) != 1:
            raise ValueError('Knowledge version not found')
        row = matches[0]
        if row['status'] != 'pending':
            raise ValueError('A reviewed version is immutable')
        row.update(status='approved' if approved else 'rejected', reviewer=reviewer, reviewed_at=reviewed_at,
                   review_note=note)
        return copy.deepcopy(row)

    def retrieve(self, final_ingredient_ids):
        """Return approved current knowledge for the final recipe ingredients only.

        `by_ingredient` marks each final ingredient as available or not_found so the
        caller can omit explanations per ingredient. Re-run after any recipe change.
        """
        if (not isinstance(final_ingredient_ids, (list, tuple, set))
                or not all(isinstance(i, str) and i.strip() for i in final_ingredient_ids)):
            raise ValueError('Final ingredient IDs must be non-empty strings')
        wanted = set(final_ingredient_ids)
        latest = {}
        for record in self.records:
            latest[record['knowledge_id']] = record
        records = [copy.deepcopy(r) for r in latest.values()
                   if r['ingredient_id'] in wanted and r.get('status') == 'approved'
                   and r.get('reviewer') and r.get('reviewed_at') and r.get('review_note')]
        found = {r['ingredient_id'] for r in records}
        return {'status': 'available' if records else 'not_found', 'records': records,
                'by_ingredient': {i: 'available' if i in found else 'not_found' for i in sorted(wanted)}}

    def save(self, path):
        Path(path).write_text(json.dumps(self.records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    @classmethod
    def load(cls, path, ingredient_ids):
        # Read/parse failures deliberately propagate: service unavailable != no knowledge.
        return cls(ingredient_ids, json.loads(Path(path).read_text(encoding='utf-8')))


def retrieve_knowledge(path, ingredient_ids, final_ingredient_ids):
    """Load the store and retrieve; a broken store is `unavailable`, never `not_found`."""
    try:
        store = KnowledgeStore.load(path, ingredient_ids)
    except (OSError, ValueError) as exc:
        return {'status': 'unavailable', 'records': [], 'by_ingredient': {},
                'error': type(exc).__name__}
    return store.retrieve(final_ingredient_ids)


def verify_citations(citations, snapshot):
    """Check that an explanation cites only knowledge actually returned in `snapshot`.

    Each citation needs knowledge_id, source_version and ingredient_id matching one
    retrieved record. Anything else (invented ID, wrong ingredient, unreviewed or
    superseded version) is listed in `invalid` and must not be shown.
    """
    allowed = {(r['knowledge_id'], r['source_version'], r['ingredient_id'])
               for r in (snapshot or {}).get('records', [])}
    valid, invalid = [], []
    for citation in citations if isinstance(citations, list) else [citations]:
        key = (tuple(citation.get(k) for k in ('knowledge_id', 'source_version', 'ingredient_id'))
               if isinstance(citation, dict) else None)
        (valid if key in allowed else invalid).append(copy.deepcopy(citation))
    return {'valid': not invalid, 'citations': valid, 'invalid': invalid}
