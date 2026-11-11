"""Refresh exact former product URLs, retaining identity/package gates in V2 only.

Input is the locally recovered price-only export, never credentials or orders.
No historical query timestamp is rewritten: every saved quote is a fresh fetch.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
sys.path.insert(0, '/app/backend')
from app.core.config import settings
from app.db.session import SessionLocal
from app.api.sim_market import get_catalog
from app.pricing_scheduler import create_repository
from app.source_adapters.ingredient_database import load_ingredient_queries
from app.source_adapters.price_collection import PriceCollectionService
from app.source_adapters.price_models import PriceQuote, QuoteStatus
from app.source_adapters.price_scope import quote_unusable_reason
from app.source_adapters.pxmart import PxMartAdapter, parse_products, candidate_from_raw, build_price_quote_from_candidates
from app.source_adapters.product_matching import POLICY_VERSION, identity_conflicts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-export', required=True)
    parser.add_argument('--targets', required=True)
    parser.add_argument('--helper-dir', required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.helper_dir)
    from recheck_t02_prices import PublicPages, public_url
    repository = create_repository(expected_recipe_release_id=settings.generation_recipe_release_id)
    for engine in (repository.engine, SessionLocal.kw['bind']):
        with engine.connect() as c:
            if c.scalar(text('SELECT current_database()')) != 'meal_planner_1102_v3_1111_update_v2':
                raise RuntimeError('Only the V2 isolated database may be used')
    old = json.loads(Path(args.old_export).read_text(encoding='utf-8-sig'))
    targets = set(json.loads(Path(args.targets).read_text(encoding='utf-8-sig')))
    bindings = defaultdict(set)
    for row in old['price_snapshot_foods']:
        if row['recipe_release_id'] == settings.generation_recipe_release_id:
            bindings[row['snapshot_id']].add(row['food_id'])
    urls = defaultdict(list)
    for row in sorted(old['price_quote_snapshots'], key=lambda r:r['queried_at'], reverse=True):
        if row['status'] != 'success' or identity_conflicts(row['canonical_name'], row['product_name'] or ''):
            continue
        payload = row['quote_payload']
        address = payload.get('product_url')
        if not address:
            address = next((c.get('product_url') for c in payload.get('candidates', []) if c.get('product_id') == row['product_id']), None)
        if not address:
            continue
        try:
            address = public_url(address)
        except ValueError:
            continue
        if 'search' in urlsplit(address).path:
            continue
        for fid in bindings[row['id']] & targets:
            key = (row['provider'], fid)
            if address not in urls[key] and len(urls[key]) < 3:
                urls[key].append(address)
    # Existing reviewed raw-rice SKU (the FOOD000345 rule) is also raw rice,
    # not the rice-burger result previously attached to FOOD000272.
    urls['pxmart', 'FOOD000272'].insert(0, 'https://shop.pxgo.com.tw/hourArrive/goods/239259-26010008-4710265010961')
    with httpx.Client(timeout=20, follow_redirects=False, limits=httpx.Limits(max_connections=1)) as client:
        pages = PublicPages(client)
        for provider in ('pxmart', 'uniprosperity'):
            with SessionLocal() as db:
                catalog = get_catalog(db)
            remaining = targets - {p['food_id'] for p in catalog['products']}
            queries = [q for q in load_ingredient_queries(repository.engine) if set(q.food_ids) & remaining]
            queries = [q for q in queries if any(urls[provider, fid] for fid in q.food_ids)]
            if not queries:
                continue

            class DirectAdapter(PxMartAdapter):
                def close(self):
                    pass

                def quote(self, query, **kwargs):
                    candidates = []
                    addresses = list(dict.fromkeys(u for fid in query.food_ids for u in urls[provider, fid]))
                    result = None
                    for address in addresses:
                        try:
                            response = pages.get(address)
                            documents = parse_products(response.text) if response.status_code == 200 else []
                            if len(documents) != 1:
                                print(json.dumps({'name':query.canonical_name,'url':address,'status':response.status_code,'reason':'not one product'},ensure_ascii=True),flush=True)
                                continue
                            raw = documents[0]
                            offers = raw.get('offers')
                            if isinstance(offers, list) and len(offers) != 1 or isinstance(offers, dict) and offers.get('@type') == 'AggregateOffer':
                                continue
                            candidate = candidate_from_raw(query, {**raw, 'url':str(response.url)})
                            candidates.append(candidate)
                            result = build_price_quote_from_candidates(query,candidates,source_url=str(response.url),source_request_count=len(candidates))
                            result = result.model_copy(update={'retailer':provider,'source':provider+' exact product refresh'})
                            if quote_unusable_reason(result,datetime.now(timezone.utc),allow_volume=True) is None:
                                break
                        except (ValueError,httpx.HTTPError) as exc:
                            print(json.dumps({'name':query.canonical_name,'url':address,'reason':type(exc).__name__},ensure_ascii=True),flush=True)
                    if result is None:
                        result = PriceQuote(canonical_name=query.canonical_name,food_ids=query.food_ids,
                            source_url=addresses[0],queried_at=datetime.now(timezone.utc),status=QuoteStatus.NOT_FOUND,
                            missing_reason='previous exact product URLs unavailable or no longer one product')
                    print(json.dumps({'food_ids':query.food_ids,'name':query.canonical_name,'status':result.status.value,'product':result.product_name},ensure_ascii=True),flush=True)
                    return result

            service = PriceCollectionService(repository=repository,adapter_factory=lambda:DirectAdapter(client=pages,retries=0),
                ingredient_loader=lambda:queries,seasoning_food_ids=set(),delay_seconds=2)
            run_key = f'v2-recover83-direct-{POLICY_VERSION}-{datetime.now(timezone.utc):%Y%m%d}:{provider}'
            result = service._collect(provider=provider,run_key=run_key,
                adapter_factory=service.adapter_factory,trigger_type='manual',scheduled_for=datetime.now(timezone.utc),
                limit=None,batch_size=None,food_ids=remaining)
            print(json.dumps(result.__dict__,ensure_ascii=True,default=str),flush=True)


if __name__ == '__main__':
    main()
