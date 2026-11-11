"""Read-only recheck of existing broth evidence; does not grant DB approval.

Requires scripts/recheck_t02_prices.py on PYTHONPATH. The portable config
approval is not itself a management manual_revision_id/review_decision_id.
"""
import hashlib
import json
from pathlib import Path

import httpx
from recheck_t02_prices import load_confirmed, PublicPages, recheck_confirmed


def main():
    raw = Path('/app/config/t02_confirmed_recheck.json').read_bytes()
    _, rules = load_confirmed(raw)
    rule = next(r for r in rules if r.food_id == 'FOOD000859')
    if (rule.kind, rule.sku, rule.quantity, rule.unit) != ('retail', '19672', 500, 'ml'):
        raise RuntimeError('Existing exact broth approval changed; stop')
    with httpx.Client(timeout=20, follow_redirects=False,
                      limits=httpx.Limits(max_connections=1)) as client:
        result = recheck_confirmed(rule, PublicPages(client))
    result['approval_config_sha256'] = hashlib.sha256(raw).hexdigest()
    result['database_written'] = False
    result['runtime_gap'] = 'Existing file approval needs management provenance binding; g/mL conversion remains unknown'
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
