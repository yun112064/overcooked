"""Explicit import/review/index commands. No automatic approval or model downloads."""
import argparse
import json
import os

import httpx

from knowledge_rag.store import KnowledgeStore, MODEL
from knowledge_rag.importers import load_records

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','import','review','index'])
    parser.add_argument('--file')
    parser.add_argument('--dry-run',action='store_true',help='Validate an import file without DB/model calls')
    parser.add_argument('--knowledge-id')
    parser.add_argument('--source-version')
    parser.add_argument('--reviewer')
    parser.add_argument('--note')
    parser.add_argument('--approve',action='store_true')
    parser.add_argument('--index-version')
    parser.add_argument('--provider',choices=['ollama','openai'],default='ollama')
    args=parser.parse_args()
    if args.dry_run and args.action!='import':
        parser.error('--dry-run is supported for import only')
    records=[]
    if args.action=='import':
        if not args.file:
            parser.error('import requires --file')
        try:
            records=load_records(args.file)
        except ValueError as exc:
            parser.error(str(exc))
        if args.dry_run:
            print(json.dumps(dict(validated=len(records),database_written=False,embedding_called=False)))
            return
    local=None
    if args.provider=='ollama' and args.action=='index':
        from knowledge_rag.local_embedding import OllamaEmbedder
        local=OllamaEmbedder(os.getenv('OLLAMA_URL','http://127.0.0.1:11434'))
    model=local.model if local is not None else ('embeddinggemma:latest' if args.provider=='ollama' else MODEL)
    store=KnowledgeStore(os.environ['KNOWLEDGE_DATABASE_URL'],embedding_model=model)
    if args.action=='init':
        store.initialize()
    elif args.action=='import':
        try:
            print(json.dumps(store.import_records(records)))
        except ValueError as exc:
            parser.error(str(exc))
    elif args.action=='review':
        if not all([args.knowledge_id,args.source_version,args.reviewer,args.note]):
            parser.error('review requires --knowledge-id, --source-version, --reviewer, --note')
        print(store.review(args.knowledge_id,args.source_version,reviewer=args.reviewer,
                           approved=args.approve,note=args.note))
    else:
        if not args.index_version:
            parser.error('index requires --index-version')
        if local is not None:
            print(store.build_index(args.index_version,model,local.documents))
            return
        # Only cloud has the provider key and the T10 ledger. No direct paid fallback.
        cloud=os.environ['KNOWLEDGE_CLOUD_URL'].rstrip('/')
        token=os.environ['KNOWLEDGE_INTERNAL_TOKEN']
        batch=0
        def embed(texts):
            nonlocal batch
            response=httpx.post(cloud+'/internal/knowledge/embeddings',
                headers={'Authorization':'Bearer '+token},
                json=dict(index_version=args.index_version,batch_no=batch,texts=texts),timeout=90)
            response.raise_for_status()
            data=response.json()
            if data.get('model')!=MODEL:
                raise ValueError('Cloud model mismatch')
            batch+=1
            return data['vectors']
        print(store.build_index(args.index_version,MODEL,embed))

if __name__=='__main__':
    main()
