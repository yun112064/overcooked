"""Immutable source versions; reviewed current records; ID-filtered pgvector search."""
import json
import math
from pathlib import Path
import re
from typing import Any, LiteralString, cast
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
TEMPLATE = 't11-direct-ingredient-v1'
FIELDS = ('knowledge_id','ingredient_id','source_version','excerpt','source_url','rights','conditions')
FOOD = re.compile(r'FOOD\d{6}\Z')

class KnowledgeStoreError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validate_record(record):
    """Shared source contract for JSON, spreadsheet and direct callers."""
    if not isinstance(record, dict) or any(
            not isinstance(record.get(k), str) or not record[k].strip() for k in FIELDS):
        raise ValueError('Knowledge requires IDs, source, text, rights and conditions')
    if not FOOD.fullmatch(record['ingredient_id']):
        raise ValueError('Expected final FOOD ID')
    parsed = urlparse(record['source_url'])
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        raise ValueError('Invalid source URL')
    return {k: v for k, v in record.items() if k not in ('status', 'reviewer', 'review_note', 'reviewed_at')}

def vector(value, dimensions=DIMENSIONS):
    if (not isinstance(value,list) or len(value)!=dimensions
            or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in value)
            or not any(value)):
        raise ValueError(f'Expected {dimensions} finite, nonzero vector components')
    return json.dumps(value)

class KnowledgeStore:
    def __init__(self, dsn, *, embedding_model=MODEL):
        self.dsn = dsn
        self.model = embedding_model
        self.dimensions = DIMENSIONS
        self.template = TEMPLATE
        if re.fullmatch(r'embeddinggemma:latest(?:@[0-9a-f]{64})?', embedding_model):
            self.dimensions = 768
            self.template = 't11-embeddinggemma-document-query-v1'
        elif embedding_model != MODEL:
            raise ValueError('Unsupported embedding profile')

    def connect(self):
        return psycopg.Connection[dict[str, Any]].connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def initialize(self):
        with self.connect() as conn:
            # Bundled, trusted schema; never caller-supplied SQL.
            schema = cast(LiteralString, Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            if self.dimensions == 768:
                schema = cast(LiteralString, schema.replace('vector(1536)', 'vector(768)'))
            conn.execute(sql.SQL(schema))
            dimension = conn.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid="
                "'knowledge_embeddings'::regclass AND attname='embedding'").fetchone()
            if dimension is None or dimension['atttypmod'] != self.dimensions:
                raise ValueError('Use a separate knowledge database for this embedding profile')

    def import_record(self, record):
        payload = validate_record(record)
        try:
            with self.connect() as conn:
                conn.execute('INSERT INTO knowledge_records (knowledge_id,source_version,ingredient_id,payload) '
                             'VALUES (%s,%s,%s,%s)',
                             (record['knowledge_id'],record['source_version'],record['ingredient_id'],Jsonb(payload)))
        except psycopg.errors.UniqueViolation:
            raise ValueError('Immutable version already exists; use a new source_version') from None

    def import_records(self, records):
        """Atomic file import: identical versions are skipped, changed ones fail."""
        prepared = [(record, validate_record(record)) for record in records]
        imported = 0
        with self.connect() as conn:
            for record, payload in prepared:
                inserted = conn.execute(
                    'INSERT INTO knowledge_records (knowledge_id,source_version,ingredient_id,payload) '
                    'VALUES (%s,%s,%s,%s) ON CONFLICT (knowledge_id,source_version) DO NOTHING '
                    'RETURNING knowledge_id',
                    (record['knowledge_id'], record['source_version'], record['ingredient_id'], Jsonb(payload))).fetchone()
                if inserted:
                    imported += 1
                else:
                    existing = conn.execute(
                        'SELECT payload FROM knowledge_records WHERE knowledge_id=%s AND source_version=%s',
                        (record['knowledge_id'], record['source_version'])).fetchone()
                    if existing is None or existing['payload'] != payload:
                        raise ValueError('Immutable version changed; use a new source_version')
        return dict(imported=imported, unchanged=len(prepared)-imported, status='pending')

    def review(self, kid, version, *, reviewer, approved, note):
        if not isinstance(approved,bool) or not all(isinstance(x,str) and x.strip() for x in (reviewer,note)):
            raise ValueError('Reviewer, decision and source-check note required')
        with self.connect() as conn:
            row = conn.execute('UPDATE knowledge_records SET status=%s,reviewer=%s,review_note=%s,reviewed_at=now() '
                               "WHERE knowledge_id=%s AND source_version=%s AND status='pending' RETURNING status",
                               ('approved' if approved else 'rejected',reviewer,note,kid,version)).fetchone()
            if row is None:
                raise ValueError('Only an existing pending version can be reviewed')
            return row

    def build_index(self, index_version, embedding_model, embed, *, batch_size=16):
        """Paid adapters reserve/settle via T10; local adapters make no paid calls."""
        if not isinstance(index_version,str) or not index_version.strip() or embedding_model != self.model:
            raise ValueError('Index version and supported embedding model required')
        if not isinstance(batch_size,int) or not 1<=batch_size<=100:
            raise ValueError('batch_size must be 1-100')
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
                                "ORDER BY knowledge_id,seq DESC) c WHERE status='approved' ORDER BY knowledge_id").fetchall()
            conn.execute('INSERT INTO knowledge_indexes VALUES (%s,%s,%s,%s,0)',
                         (index_version,embedding_model,self.template,'building'))
        encoded=[]
        for start in range(0,len(rows),batch_size):
            batch=rows[start:start+batch_size]
            result=embed([json.dumps(r['payload'],ensure_ascii=False,sort_keys=True) for r in batch])
            if len(result)!=len(batch):
                raise ValueError('Embedding count mismatch')
            encoded.extend(vector(v, self.dimensions) for v in result)
        with self.connect() as conn:
            # Serialize publishers. Failure leaves the previous active index intact.
            conn.execute('SELECT pg_advisory_xact_lock(110027)')
            for r,v in zip(rows,encoded):
                conn.execute('INSERT INTO knowledge_embeddings VALUES (%s,%s,%s,%s::vector)',
                             (index_version,r['knowledge_id'],r['source_version'],v))
            conn.execute("UPDATE knowledge_indexes SET status='retired' WHERE status='active'")
            conn.execute("UPDATE knowledge_indexes SET status='active',record_count=%s WHERE index_version=%s",
                         (len(rows),index_version))
        return dict(index_version=index_version,embedding_model=embedding_model,records=len(rows))

    def search(self, food_ids, query_embedding, embedding_model, *, index_version=None, limit_per_ingredient=3):
        if not isinstance(food_ids,(list,tuple)) or not food_ids or any(not isinstance(i,str) or not FOOD.fullmatch(i) for i in food_ids):
            raise ValueError('Final FOOD IDs required')
        if isinstance(limit_per_ingredient,bool) or not isinstance(limit_per_ingredient,int) or not 1<=limit_per_ingredient<=10:
            raise ValueError('limit_per_ingredient must be 1-10')
        q=vector(query_embedding, self.dimensions)
        wanted=sorted(set(food_ids))
        try:
            with self.connect() as conn:
                index=conn.execute("SELECT * FROM knowledge_indexes WHERE "
                                   "(%s::text IS NULL AND status='active') OR "
                                   "(index_version=%s AND status IN ('active','retired'))",
                                   (index_version,index_version)).fetchone()
                if index is None:
                    raise KnowledgeStoreError('NO_ACTIVE_INDEX' if index_version is None else 'INDEX_NOT_AVAILABLE')
                if index['embedding_model']!=embedding_model:
                    raise KnowledgeStoreError('EMBEDDING_MODEL_MISMATCH')
                hits=conn.execute('''WITH current AS (
                    SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records ORDER BY knowledge_id,seq DESC
                ), ranked AS (
                    SELECT c.*, e.embedding <=> %(q)s::vector AS distance,
                        row_number() OVER (PARTITION BY c.ingredient_id ORDER BY e.embedding <=> %(q)s::vector,c.knowledge_id) AS rn
                    FROM current c JOIN knowledge_embeddings e
                    ON e.knowledge_id=c.knowledge_id AND e.source_version=c.source_version AND e.index_version=%(idx)s
                    WHERE c.ingredient_id=ANY(%(ids)s) AND c.status='approved'
                ) SELECT * FROM ranked WHERE rn<=%(k)s ORDER BY ingredient_id,distance,knowledge_id''',
                    dict(q=q,idx=index['index_version'],ids=wanted,k=limit_per_ingredient)).fetchall()
        except psycopg.Error:
            raise KnowledgeStoreError('STORE_UNAVAILABLE') from None
        records=[dict(r['payload'],status=r['status'],reviewer=r['reviewer'],
                      reviewed_at=r['reviewed_at'].isoformat(),distance=float(r['distance'])) for r in hits]
        found={r['ingredient_id'] for r in records}
        return dict(status='available' if records else 'not_found',records=records,
                    by_ingredient={i:'available' if i in found else 'not_found' for i in wanted},
                    index_version=index['index_version'],embedding_model=index['embedding_model'],
                    template_version=index['template_version'])
