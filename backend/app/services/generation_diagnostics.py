"""Private, bounded identity-validation evidence; never persist model prose or secrets."""
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone

from app.core.config import settings

CODES = {'INGREDIENT_ID_MISMATCH', 'INGREDIENT_AMBIGUOUS', 'INGREDIENT_UNKNOWN',
         'INGREDIENT_UNRESOLVED', 'INGREDIENT_NOT_ALLOWED', 'INVALID_QUANTITY'}


def dictionary_version(foods):
    payload = {'names': foods.names, 'index': {k: sorted(v) for k, v in foods.index.items()},
               'usage': foods.usage, 'ambiguous_names': sorted(getattr(foods, 'ambiguous_names', set()))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _path(job, attempt):
    job_id = str(uuid.UUID(str(job.id)))
    return settings.project_data_root / 'generation-diagnostics' / job_id / f'{int(attempt)}.json'


def _binding(foods):
    return {'dictionary_sha256': dictionary_version(foods),
            'recipe_release_id': settings.generation_recipe_release_id,
            'nutrition_import_id': settings.generation_nutrition_import_id,
            'mapping_version': settings.generation_food_mapping_version,
            'policy_sha256': settings.nutrition_policy_manifest_sha256}


def record_failure(job, foods, problems):
    # Only retain dictionary-known names and structured IDs, never arbitrary model text.
    from app.services.recipe_import import normalize_name
    errors = []
    for p in problems[:40]:
        code = p.get('code')
        if code not in CODES:
            continue
        name = p.get('ingredient', '')
        candidates = sorted(foods.index.get(normalize_name(name), set()))
        submitted = p.get('submitted_food_id')
        detail = {'code': code,
                       'ingredient': name if name in foods.names.values() else '[noncanonical]',
                       'submitted_food_id': submitted if isinstance(submitted, str) and re.fullmatch(r'FOOD\d{6}', submitted) else None,
                       'candidate_food_ids': candidates,
                       'candidate_names': sorted({foods.names[i] for i in candidates})}
        if code == 'INVALID_QUANTITY':
            quantity = p.get('grams_per_n')
            detail['grams_per_n'] = quantity if isinstance(quantity, (int, float)) and math.isfinite(quantity) else None
            detail['quantity_rule'] = '0 < grams_per_n <= 2000; omit unused ingredients'
        errors.append(detail)
    if not errors:
        return
    record = {'schema': 'generation-identity-diagnostic/v1', 'job_id': str(job.id),
              'attempt': job.attempts, 'created_at': datetime.now(timezone.utc).isoformat(),
              'binding': _binding(foods), 'errors': errors}
    path = _path(job, job.attempts)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        pass  # Preserve original evidence for this attempt.


def retry_feedback(job, foods):
    if job.attempts < 2:
        return []
    path = _path(job, job.attempts - 1)
    try:
        if path.stat().st_size > 65536:
            return []
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if (record.get('schema') != 'generation-identity-diagnostic/v1'
            or record.get('job_id') != str(job.id)
            or record.get('attempt') != job.attempts - 1
            or record.get('binding') != _binding(foods)):
        return []
    safe_names = set(foods.allowed_names(foods.names))
    return [{'code': e['code'], 'ingredient': e['ingredient'],
             'candidate_names': [n for n in e['candidate_names'] if n in safe_names],
             **({'quantity_rule': e['quantity_rule']} if e.get('quantity_rule') else {})}
            for e in record['errors']]
