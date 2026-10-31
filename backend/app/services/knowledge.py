"""T11 cloud boundary: T10-controlled embeddings, remote retrieval, validated citations."""
import copy
import math
import time
import uuid
import re

import httpx

from app.services import spend
from app.core.logging_config import process_logger

MODEL='text-embedding-3-small'
DIMENSIONS=1536

def validate_vectors(value,count):
    if not isinstance(value,list) or len(value)!=count:
        raise ValueError('Embedding count mismatch')
    for v in value:
        if (not isinstance(v,list) or len(v)!=DIMENSIONS or not any(v)
                or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v)):
            raise ValueError('Invalid embedding')
    return value

class OpenAIEmbedder:
    model=MODEL
    def __init__(self,api_key):
        self.api_key=api_key

    def embed(self,texts):
        try:
            res=httpx.post('https://api.openai.com/v1/embeddings',
                           headers={'Authorization':'Bearer '+self.api_key},
                           json={'model':self.model,'input':texts,'dimensions':DIMENSIONS},timeout=60)
        except httpx.ConnectError:
            raise spend.CallNotBilled('Embedding connection failed before request') from None
        if 400<=res.status_code<500:
            raise spend.CallNotBilled('Embedding request rejected')
        res.raise_for_status()
        body=res.json()
        items=sorted(body['data'],key=lambda i:i['index'])
        if body.get('model')!=self.model or [i['index'] for i in items]!=list(range(len(texts))):
            raise ValueError('Embedding response model/index mismatch')
        result=validate_vectors([i['embedding'] for i in items],len(texts))
        tokens=body['usage']['prompt_tokens']
        if isinstance(tokens,bool) or not isinstance(tokens,int) or tokens<0:
            raise ValueError('Invalid embedding usage')
        return result,dict(input_tokens=tokens,output_tokens=0)

def embed_index_batch(factory,embedder,index_version,batch_no,texts):
    if not texts or len(texts)>100 or any(not isinstance(t,str) or not t or len(t.encode())>8000 for t in texts):
        raise ValueError('Index batch must contain 1-100 texts, each at most 8000 UTF-8 bytes')
    if embedder.model!=MODEL:
        raise ValueError('Unsupported embedding model')
    return spend.paid_call(factory,call_key=f'knowledge-index:{index_version}:{batch_no}',
        purpose='knowledge_index_embedding',provider='openai',model=embedder.model,
        max_input_tokens=sum(len(t.encode()) for t in texts),max_output_tokens=0,
        call=lambda: _embed(embedder,texts))

def _embed(embedder,texts):
    values,usage=embedder.embed(texts)
    return validate_vectors(values,len(texts)),usage

class HttpKnowledgeClient:
    def __init__(self,url,token,*,transport=None):
        self.url=url.rstrip('/')
        self.token=token
        self.transport=transport

    def search(self,food_ids,query_embedding,embedding_model,*,index_version=None):
        with httpx.Client(transport=self.transport,timeout=15) as client:
            res=client.post(self.url+'/v1/search',headers={'Authorization':'Bearer '+self.token},
                json=dict(food_ids=food_ids,query_embedding=query_embedding,
                          embedding_model=embedding_model,index_version=index_version))
            res.raise_for_status()
            return res.json()

    def retrieve(self,food_ids,query_text,*,index_version=None):
        with httpx.Client(transport=self.transport,timeout=120) as client:
            res=client.post(self.url+'/v1/retrieve',headers={'Authorization':'Bearer '+self.token},
                json=dict(food_ids=food_ids,query_text=query_text,index_version=index_version))
            res.raise_for_status()
            return res.json()

def unavailable(food_ids,reason):
    return dict(status='unavailable',reason=reason,records=[],final_food_ids=food_ids,
                by_ingredient={i:'unavailable' for i in food_ids})

def retrieve_knowledge(*,final_food_ids,query_text,client,embedder,paid_call,index_version=None,call_name='embed_query',
                       job_id=None,trace_id=None):
    food_ids=sorted(set(final_food_ids))
    if not food_ids:
        return dict(status='not_found',records=[],by_ingredient={},final_food_ids=[])
    if client is None:
        return unavailable(food_ids,'NOT_CONFIGURED')
    if any(not isinstance(i,str) or len(i)!=10 or not i.startswith('FOOD') or not i[4:].isdigit() for i in food_ids):
        raise ValueError('Final FOOD IDs required')
    if not isinstance(query_text,str) or not query_text.strip() or len(query_text.encode())>8000:
        raise ValueError('Query must be 1-8000 UTF-8 bytes')
    started=time.monotonic()
    trace_id=trace_id or uuid.uuid4().hex
    def log_failure(error_code):
        # Fixed categories only: never write exception text, provider payloads or query data.
        process_logger.warning('knowledge_retrieval_failed',extra=dict(
            event='knowledge_retrieval_failed',error_code=error_code,trace_id=trace_id,
            job_id=str(job_id) if job_id is not None else None,
            duration_ms=int((time.monotonic()-started)*1000)))
    if embedder is not None:
        try:
            values=paid_call(name=call_name,purpose='knowledge_query_embedding',provider='openai',model=embedder.model,
                            max_input_tokens=len(query_text.encode()),max_output_tokens=0,
                            call=lambda:_embed(embedder,[query_text]))
        except spend.CallNotBilled:
            log_failure('EMBEDDING_UNAVAILABLE')
            return unavailable(food_ids,'EMBEDDING_UNAVAILABLE')
    # BudgetExhausted/UncertainBilling must propagate to T10; never pretend they are zero results.
    try:
        snapshot=(client.search(food_ids,values[0],embedder.model,index_version=index_version)
                  if embedder is not None else client.retrieve(food_ids,query_text,index_version=index_version))
        if not isinstance(snapshot,dict):
            raise ValueError('Invalid response object')
        records=snapshot['records']
        model=snapshot.get('embedding_model')
        model_valid=(model==embedder.model if embedder is not None else
                     isinstance(model,str) and re.fullmatch(r'embeddinggemma:latest@[0-9a-f]{64}',model)
                     and snapshot.get('embedding_dimensions')==768
                     and snapshot.get('template_version')=='t11-embeddinggemma-document-query-v1')
        if not model_valid or not snapshot.get('index_version'):
            raise ValueError('Model/index mismatch')
        if index_version is not None and snapshot['index_version']!=index_version:
            raise ValueError('Pinned index changed')
        if not isinstance(records,list) or snapshot.get('status') not in ('available','not_found'):
            raise ValueError('Invalid response')
        found=set()
        seen=set()
        for r in records:
            if not isinstance(r,dict):
                raise ValueError('Invalid record object')
            if r['ingredient_id'] not in food_ids or r.get('status')!='approved':
                raise ValueError('Wrong ingredient/unreviewed record')
            if any(not isinstance(r.get(k),str) or not r[k].strip() for k in
                   ('knowledge_id','source_version','excerpt','source_url','rights','conditions','reviewer','reviewed_at')):
                raise ValueError('Incomplete source record')
            key=(r['knowledge_id'],r['source_version'],r['ingredient_id'])
            if key in seen:
                raise ValueError('Duplicate knowledge record')
            seen.add(key)
            found.add(r['ingredient_id'])
        expected={i:'available' if i in found else 'not_found' for i in food_ids}
        if snapshot.get('by_ingredient')!=expected or snapshot['status']!=('available' if records else 'not_found'):
            raise ValueError('Inconsistent retrieval state')
        return dict(copy.deepcopy(snapshot),final_food_ids=food_ids)
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
        if isinstance(exc,httpx.HTTPStatusError):
            code='KNOWLEDGE_AUTH_FAILED' if exc.response.status_code in (401,403) else 'KNOWLEDGE_HTTP_FAILED'
        elif isinstance(exc,httpx.RequestError):
            code='KNOWLEDGE_CONNECTION_FAILED'
        else:
            code='KNOWLEDGE_INVALID_RESPONSE'
        log_failure(code)
        return unavailable(food_ids,'KNOWLEDGE_UNAVAILABLE')

def verify_citations(citations,snapshot):
    allowed={(r['knowledge_id'],r['source_version'],r['ingredient_id']):r for r in snapshot.get('records',[])
             if snapshot.get('status')=='available'}
    valid,invalid=[],[]
    for c in citations if isinstance(citations,list) else [citations]:
        key=(c.get('knowledge_id'),c.get('source_version'),c.get('ingredient_id')) if isinstance(c,dict) else None
        if key is not None and all(isinstance(value,str) for value in key) and key in allowed:
            # Only the retrieved snapshot is authoritative for citation content.
            valid.append(copy.deepcopy(allowed[key]))
        else:
            invalid.append(copy.deepcopy(c))
    return dict(valid=not invalid,citations=valid,invalid=invalid)

def retrieve_for_job(ctx,final_food_ids,query_text,*,index_version=None,call_name='embed_query'):
    """T14 integration seam: explicit final ingredients and the existing JobContext budget."""
    from app.core.config import settings
    client=(HttpKnowledgeClient(settings.knowledge_service_url,settings.knowledge_service_token)
            if settings.knowledge_service_url and settings.knowledge_service_token else None)
    if settings.knowledge_provider=='openai' and not settings.openai_api_key:
        return unavailable(sorted(set(final_food_ids)),'NOT_CONFIGURED')
    embedder=OpenAIEmbedder(settings.openai_api_key) if settings.knowledge_provider=='openai' else None
    return retrieve_knowledge(final_food_ids=final_food_ids,query_text=query_text,client=client,
                              embedder=embedder,paid_call=ctx.paid_call,index_version=index_version,call_name=call_name,
                              job_id=ctx.job.id)
