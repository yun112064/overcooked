"""Stage explicit source research as pending candidates, never mapping approvals."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from app.services.t04_data_feedback import fingerprint

POLICY='t04-remaining247-review-v1'


def load_plan(path,expected_sha256):
    path=Path(path);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256: raise ValueError('Review plan hash mismatch')
    plan=json.loads(raw)
    if plan.get('schema_version')!=POLICY or plan.get('policy_version')!=POLICY:
        raise ValueError('Unsupported review policy')
    if any(plan.get(k) is not False for k in ['automatic_merge','automatic_approval','automatic_nutrient_fill']):
        raise ValueError('Source research must never merge, approve or fill')
    items=plan['items']
    if len(items)!=247 or len({r['food_id'] for r in items})!=247:
        raise ValueError('Expected exactly the 247 distinct backlog foods')
    snapshot=plan['official_snapshot'];filename=Path(snapshot['file'])
    if filename.name!=snapshot['file']: raise ValueError('Unexpected evidence path')
    if hashlib.sha256((path.parent/filename).read_bytes()).hexdigest()!=snapshot['sha256']:
        raise ValueError('Official snapshot hash mismatch')
    return plan


def build_reviewed_candidates(plan,sources,catalog,recipe_release_id,import_id,plan_sha256):
    if plan['recipe_release_id']!=recipe_release_id or plan['nutrition_import_id']!=import_id:
        raise ValueError('Review release mismatch')
    lookup={s['source_key']:s for s in sources}
    foods={r['food_id']:r for r in catalog};output=[]
    for item in plan['items']:
        food=foods.get(item['food_id'])
        if not food or food['canonical_name']!=item['name']: raise ValueError('Review food identity changed')
        if item['review_status']!='pending_review': raise ValueError('Research cannot approve source mappings')
        if item['category']!='name_variant' and item['confirmation_required'] is not True:
            raise ValueError('Uncertain ingredient identities must require confirmation')
        for key in item['source_keys']:
            source=lookup.get(key)
            if not source or source['import_id']!=import_id or source['provider'] not in {'TFDA','USDA'}:
                raise ValueError('Source missing from selected original batch: '+key)
            if source.get('source_url') and urlparse(source['source_url']).scheme not in {'http','https'}:
                raise ValueError('Invalid source URL')
            evidence={'method':'explicit_remaining247_research_hypothesis','plan_sha256':plan_sha256,
                'canonical_name':item['name'],'category':item['category'],
                'confirmation_required':item['confirmation_required'],'confirmation_question':item['question'],
                'official_metadata':item.get('official_sample_metadata',{}).get(key),
                'official_snapshot_sha256':plan['official_snapshot']['sha256'],
                'official_metadata_only_not_new_nutrient_values':True,
                'automatic_approval':False,'automatic_nutrient_fill':False,
                'source_name':source['raw_data'].get('中文原名') or source['raw_data'].get('來源原文名稱')}
            fields={'recipe_release_id':recipe_release_id,'nutrition_import_id':import_id,
                'food_id':item['food_id'],'source_id':source['source_id'],'source_fingerprint':fingerprint(source),
                'policy_version':POLICY,'match_evidence':evidence,'review_status':'pending_review'}
            output.append({'candidate_id':fingerprint(fields),**fields})
    if len({r['candidate_id'] for r in output})!=len(output): raise ValueError('Duplicate research candidates')
    return output
