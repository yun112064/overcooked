"""Team computer service. No provider credentials or paid calls on this machine."""
import hmac
import os
import uuid

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException

from knowledge_rag.store import KnowledgeStore, KnowledgeStoreError

def make_app(store,token,embedder=None):
    if not token:
        raise ValueError('KNOWLEDGE_SERVICE_TOKEN required')
    app=FastAPI(title='T11 knowledge retrieval',docs_url=None,redoc_url=None,openapi_url=None)

    def authorize(authorization: str=Header(default='')):
        if not hmac.compare_digest(authorization.encode(),('Bearer '+token).encode()):
            raise HTTPException(401,'AUTH_FAILED')

    @app.post('/v1/search',dependencies=[Depends(authorize)])
    def search(body:dict):
        try:
            return store.search(body['food_ids'],body['query_embedding'],body['embedding_model'],
                                index_version=body.get('index_version'))
        except (KeyError,ValueError,TypeError):
            raise HTTPException(422,'INVALID_QUERY') from None
        except KnowledgeStoreError as exc:
            raise HTTPException(503,exc.code) from None

    @app.post('/v1/retrieve',dependencies=[Depends(authorize)])
    def retrieve(body:dict):
        if embedder is None:
            raise HTTPException(503,'LOCAL_EMBEDDING_NOT_CONFIGURED')
        from knowledge_rag.local_embedding import LocalEmbeddingUnavailable
        try:
            query = body['query_text']
            if not isinstance(query,str) or not query.strip() or len(query.encode())>8000:
                raise ValueError('Invalid query')
            food_ids = body['food_ids']
            from knowledge_rag.store import FOOD
            if not isinstance(food_ids,list) or not food_ids or any(not isinstance(f,str) or not FOOD.fullmatch(f) for f in food_ids):
                raise ValueError('Final FOOD IDs required')
        except (KeyError,ValueError,TypeError):
            raise HTTPException(422,'INVALID_QUERY') from None
        try:
            result = store.search(food_ids,embedder.query(query),embedder.model,
                                  index_version=body.get('index_version'))
            return dict(result,provider='ollama',provider_cost_usd='0',embedding_dimensions=768)
        except (httpx.HTTPError,LocalEmbeddingUnavailable,KnowledgeStoreError,KeyError,ValueError,TypeError):
            # Never log the query, token, source payload or exception text.
            from backend.app.core.logging_config import process_logger
            process_logger.warning('local_knowledge_unavailable',extra={
                'event':'local_knowledge_unavailable','trace_id':uuid.uuid4().hex})
            raise HTTPException(503,'KNOWLEDGE_UNAVAILABLE') from None
    return app

def create_app():
    from knowledge_rag.local_embedding import OllamaEmbedder
    embedder=OllamaEmbedder(os.getenv('OLLAMA_URL','http://127.0.0.1:11434'))
    return make_app(KnowledgeStore(os.environ['KNOWLEDGE_DATABASE_URL'],embedding_model=embedder.model),
                    os.environ['KNOWLEDGE_SERVICE_TOKEN'],embedder)
