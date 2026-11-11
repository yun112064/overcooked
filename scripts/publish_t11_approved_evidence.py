"""Apply only the two explicitly approved V2 evidence candidates; publication is separate."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_t11_evidence import DB, MARKER, data_root, validate_bundle

APPROVAL = ROOT / 'config/t11_evidence_approval_1111_update_v2.json'
INDEX = '1111-update-v2-reviewed-garlic-water-evidence-v1'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--publish', action='store_true')
    parser.add_argument('--data-root', default='/data/t11_evidence')
    parser.add_argument('--set-isolated-marker', action='store_true')
    args = parser.parse_args(argv)
    approval = json.loads(APPROVAL.read_text(encoding='utf-8'))
    root = data_root(args.data_root)
    bundle = validate_bundle(root, root / ('candidates-' + approval['bundle_sha256'] + '.json'),
                             approval['bundle_sha256'])
    rows = [r for r in bundle['records'] if r['knowledge_id'] in approval['approved']]
    if len(rows) != 2 or any(r['nutrition_fact'] != approval['approved'][r['knowledge_id']]
                             or r.get('license_status') == 'third_party_unconfirmed' for r in rows):
        raise ValueError('Exact user-approved candidates do not match source bundle')
    if not args.apply and not args.publish:
        print(json.dumps({'dry_run': True, 'approved_ids': list(approval['approved']),
                          'blocked_ids': list(approval['blocked']), 'index': INDEX}))
        return
    from knowledge_rag.store import KnowledgeStore
    dsn = os.environ['KNOWLEDGE_DATABASE_URL']
    url = urlsplit(dsn)
    if url.scheme != 'postgresql' or url.hostname != 't11-db' or url.path != '/' + DB:
        raise ValueError('Refusing non-V2 knowledge database')
    store = KnowledgeStore(dsn, embedding_model='embeddinggemma:latest')
    with store.connect() as conn:
        marker = conn.execute("SELECT current_database() AS name, shobj_description(oid, 'pg_database') AS marker "
                              "FROM pg_database WHERE datname=current_database()").fetchone()
        if marker['name'] != DB or marker['marker'] not in (None, MARKER):
            raise ValueError('Unexpected database identity or existing marker')
        if marker['marker'] != MARKER:
            if not args.set_isolated_marker or not args.apply:
                raise ValueError('Explicit isolated marker required before writing')
            from psycopg import sql
            conn.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DB), sql.Literal(MARKER)))
    if args.apply:
        imported = store.import_records(rows)
        for row in rows:
            current = store.get_record(row['knowledge_id'])
            if current['source_version'] != row['source_version']:
                raise ValueError('Unexpected current evidence version')
            if current['status'] == 'pending':
                store.review(row['knowledge_id'], row['source_version'], approved=True,
                             reviewer=approval['approval_id'], note=approval['authority'] + '; bundle=' + approval['bundle_sha256'])
            elif current['status'] != 'approved' or current['reviewer'] != approval['approval_id']:
                raise ValueError('Unexpected existing review; refusing override')
        print(json.dumps({'import': imported, 'approved_ids': list(approval['approved']),
                          'index_published': False, 'old_records_modified': False}))
        return
    for row in rows:
        current = store.get_record(row['knowledge_id'])
        if (not current or current['source_version'] != row['source_version']
                or current['status'] != 'approved' or current['reviewer'] != approval['approval_id']):
            raise ValueError('Only explicitly approved evidence can be published')
    from knowledge_rag.local_embedding import OllamaEmbedder
    embedder = OllamaEmbedder(os.environ['OLLAMA_URL'])
    store = KnowledgeStore(dsn, embedding_model=embedder.model)
    active = store.active_index()
    if active and active['index_version'] == INDEX:
        if active['embedding_model'] != embedder.model:
            raise ValueError('Pinned model changed; use a new reviewed index version')
        print(json.dumps({'already_published': True, 'index': active}))
        return
    def embed(payloads):
        docs = [json.loads(p) for p in payloads]
        return embedder.documents([f"{r.get('ingredient_name', r['ingredient_id'])}："
            f"{r.get('nutrition_fact') or r.get('fact_zh') or r['excerpt']}\n{r['conditions']}" for r in docs])
    result = store.build_index(INDEX, embedder.model, embed, batch_size=4)
    print(json.dumps({'index_published': True, 'index': result, 'approved_new_evidence_records': 2,
                      'old_records_modified': False}))


if __name__ == '__main__':
    main()
