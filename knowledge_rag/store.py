"""Immutable source versions; reviewed current records; ID-filtered pgvector search."""
import json
import math
from pathlib import Path
import re
from datetime import date, datetime, timezone
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
LICENSE_UNCONFIRMED = 'third_party_unconfirmed'

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
    quote = record.get('evidence_quote')
    if quote is not None and (not isinstance(quote, str) or quote not in record['excerpt']):
        raise ValueError('Evidence quote must be an exact excerpt from the stored passage')
    parsed = urlparse(record['source_url'])
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        raise ValueError('Invalid source URL')
    payload = {k: v for k, v in record.items() if k not in ('status', 'reviewer', 'review_note', 'reviewed_at')}
    return json.loads(json.dumps(payload, ensure_ascii=False, default=_json_default))


# save_classification merges these into payload after import (decision #29).
CLASSIFICATION_KEYS = frozenset(('classification', 'classification_reason', 'nutrition_fact', 'evidence_quote',
                                 'classifier_model', 'classifier_prompt_version'))


def same_imported_content(stored, payload):
    """True when a stored payload is the imported one, ignoring keys classification added later."""
    return {k: v for k, v in stored.items() if k in payload or k not in CLASSIFICATION_KEYS} == payload

def vector(value, dimensions=DIMENSIONS):
    if (not isinstance(value,list) or len(value)!=dimensions
            or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in value)
            or not any(value)):
        raise ValueError(f'Expected {dimensions} finite, nonzero vector components')
    return json.dumps(value)


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f'Unsupported JSON value: {type(value).__name__}')


def _jsonb(value):
    return Jsonb(value, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=_json_default))

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
                self._insert_record(conn, record, payload)
        except psycopg.errors.UniqueViolation:
            raise ValueError('Immutable version already exists; use a new source_version') from None

    @staticmethod
    def _insert_record(conn, record, payload):
        conn.execute('''INSERT INTO knowledge_records
            (knowledge_id,source_version,ingredient_id,payload,excerpt,source_url,rights,conditions,nutrition_fact,
             evidence_quote,fetched_at,content_provider,authorization_ref,classification,classification_reason,
             article_id,content_hash,published_at,source_name,title,license_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (record['knowledge_id'], record['source_version'], record['ingredient_id'], _jsonb(payload),
             record['excerpt'], record['source_url'], record['rights'], record['conditions'],
             record.get('nutrition_fact'), record.get('evidence_quote'), record.get('fetched_at'),
             record.get('content_provider'), record.get('authorization_ref'), record.get('classification'),
             record.get('classification_reason'), record.get('article_id'), record.get('content_hash'),
             record.get('published_at'), record.get('source_name'), record.get('title'), record.get('license_status')))

    @staticmethod
    def _record(row):
        if row is None:
            return None
        published_at = row.get('published_at')
        if isinstance(published_at, datetime):
            published_at = published_at.astimezone(timezone.utc)
        return dict(row['payload'], knowledge_id=row['knowledge_id'], source_version=row['source_version'],
                    ingredient_id=row['ingredient_id'], status=row['status'], reviewer=row['reviewer'],
                    review_note=row['review_note'], reviewed_at=row['reviewed_at'].isoformat() if row['reviewed_at'] else None,
                    excerpt=row.get('excerpt') or row['payload'].get('excerpt'),
                    source_url=row.get('source_url') or row['payload'].get('source_url'),
                    rights=row.get('rights') or row['payload'].get('rights'),
                    conditions=row.get('conditions') or row['payload'].get('conditions'),
                    nutrition_fact=row.get('nutrition_fact') or row['payload'].get('nutrition_fact'),
                    evidence_quote=row.get('evidence_quote') or row['payload'].get('evidence_quote'),
                    fetched_at=row.get('fetched_at') or row['payload'].get('fetched_at'),
                    content_provider=row.get('content_provider') or row['payload'].get('content_provider'),
                    authorization_ref=row.get('authorization_ref') or row['payload'].get('authorization_ref'),
                    classification=row.get('classification') or row['payload'].get('classification'),
                    classification_reason=row.get('classification_reason') or row['payload'].get('classification_reason'),
                    published_at=published_at,
                    source_name=row.get('source_name') or row['payload'].get('source_name'),
                    title=row.get('title') or row['payload'].get('title'),
                    article_id=row.get('article_id') or row['payload'].get('article_id'),
                    content_hash=row.get('content_hash') or row['payload'].get('content_hash'),
                    license_status=row.get('license_status') or row['payload'].get('license_status'),
                    imported_at=row['imported_at'].isoformat() if row.get('imported_at') else None)

    def get_record(self, kid):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
                               "WHERE knowledge_id=%s ORDER BY knowledge_id,seq DESC) current", (kid,)).fetchone()
        return self._record(row)

    def list_versions(self, kid):
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM knowledge_records WHERE knowledge_id=%s ORDER BY seq", (kid,)).fetchall()
        return [self._record(row) for row in rows]

    def list_records(self, *, status=None, limit=1000):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100_000:
            raise ValueError('limit must be 1-100000')
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM (SELECT DISTINCT ON (knowledge_id) * FROM knowledge_records "
                                "ORDER BY knowledge_id,seq DESC) current "
                                "WHERE (%s::text IS NULL OR status=%s) ORDER BY knowledge_id LIMIT %s",
                                (status, status, limit)).fetchall()
        return [self._record(row) for row in rows]

    def records_for_classification(self, *, reclassify=False, limit=1000):
        limit = 1000 if limit is None else limit
        return [row for row in self.list_records(limit=limit)
                if row['status'] in ('pending', 'approved')
                and (reclassify or row.get('classification') is None)]

    def approved_non_nutrition(self):
        return [row for row in self.list_records(status='approved', limit=100_000)
                if row.get('classification') in ('not_nutrition', 'invalid')]

    def save_classification(self, kid, version, outcome, *, model, prompt_version):
        if outcome.get('classification') not in ('nutrition', 'not_nutrition', 'invalid'):
            raise ValueError('Invalid classification')
        with self.connect() as conn:
            row = conn.execute("UPDATE knowledge_records SET payload=payload || %s, classification=%s, "
                "classification_reason=%s, classified_at=now(), classifier_model=%s, classifier_prompt_version=%s, "
                "status=CASE WHEN status='pending' AND %s <> 'nutrition' THEN 'rejected' ELSE status END, "
                "reviewer=CASE WHEN status='pending' AND %s <> 'nutrition' THEN 'classifier' ELSE reviewer END, "
                "review_note=CASE WHEN status='pending' AND %s <> 'nutrition' THEN %s ELSE review_note END, "
                "reviewed_at=CASE WHEN status='pending' AND %s <> 'nutrition' THEN now() ELSE reviewed_at END "
                "WHERE knowledge_id=%s AND source_version=%s AND status IN ('pending','approved') RETURNING status",
                (_jsonb({'classification': outcome['classification'], 'classification_reason': outcome.get('reason'),
                        'nutrition_fact': outcome.get('nutrition_fact'), 'evidence_quote': outcome.get('evidence_quote'),
                        'classifier_model': model, 'classifier_prompt_version': prompt_version}),
                 outcome['classification'], outcome.get('reason'), model, prompt_version,
                 outcome['classification'], outcome['classification'], outcome['classification'],
                 'automatic rejection: ' + str(outcome.get('reason') or 'not nutrition'),
                 outcome['classification'], kid, version)).fetchone()
        if row is None:
            raise ValueError('Knowledge version not found or no longer classifiable')
        return row

    def revoke_approval(self, kid, version, *, reason):
        with self.connect() as conn:
            current = conn.execute("SELECT reviewer,review_note FROM knowledge_records WHERE knowledge_id=%s "
                                   "AND source_version=%s AND status='approved' FOR UPDATE", (kid, version)).fetchone()
            if current is None:
                raise ValueError('Only an approved version can be revoked')
            note = f'{reason}；撤回原核准者={current["reviewer"]}；原核准紀錄={current["review_note"]}'
            conn.execute("UPDATE knowledge_records SET status='rejected',reviewer='classifier',review_note=%s,"
                         "reviewed_at=now() WHERE knowledge_id=%s AND source_version=%s AND status='approved'",
                         (note, kid, version))
            return {'status': 'rejected'}

    def save_article(self, article):
        required = ('source_name', 'article_id', 'url', 'status')
        if not isinstance(article, dict) or any(not isinstance(article.get(key), str) or not article[key]
                                                for key in required):
            raise ValueError('Article source, ID, URL and status are required')
        with self.connect() as conn:
            conn.execute("INSERT INTO source_articles (source_name,article_id,url,title,published_at,modified_at,"
                         "content_hash,status,matched_food_ids) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                         "ON CONFLICT (source_name,article_id) DO UPDATE SET url=EXCLUDED.url,title=EXCLUDED.title,"
                         "published_at=EXCLUDED.published_at,modified_at=EXCLUDED.modified_at,"
                         "content_hash=EXCLUDED.content_hash,status=EXCLUDED.status,"
                         "matched_food_ids=EXCLUDED.matched_food_ids,last_fetched_at=now(),"
                         "last_changed_at=CASE WHEN source_articles.content_hash IS DISTINCT FROM EXCLUDED.content_hash "
                         "THEN now() ELSE source_articles.last_changed_at END",
                         (article['source_name'], article['article_id'], article['url'], article.get('title'),
                          article.get('published_at'), article.get('modified_at'), article.get('content_hash'),
                          article['status'], article.get('matched_food_ids') or []))

    def known_article_ids(self, source_name):
        with self.connect() as conn:
            rows = conn.execute("SELECT article_id FROM source_articles WHERE source_name=%s "
                                "AND status IN ('matched','no_match')", (source_name,)).fetchall()
        return {row['article_id'] for row in rows}

    def status_summary(self):
        with self.connect() as conn:
            counts = conn.execute("SELECT status,count(*) AS n FROM (SELECT DISTINCT ON (knowledge_id) status "
                                  "FROM knowledge_records ORDER BY knowledge_id,seq DESC) q GROUP BY status").fetchall()
            index = conn.execute("SELECT index_version,embedding_model,template_version,record_count FROM knowledge_indexes "
                                 "WHERE status='active'").fetchone()
            articles = conn.execute("SELECT status,count(*) AS n FROM source_articles GROUP BY status").fetchall()
            license_holds = conn.execute("SELECT count(*) AS n FROM knowledge_records WHERE status='pending' "
                                         "AND license_status=%s", (LICENSE_UNCONFIRMED,)).fetchone()['n']
        records = {'pending': 0, 'approved': 0, 'rejected': 0}
        records.update({row['status']: row['n'] for row in counts})
        return {'records': records, 'active_index': dict(index) if index else None,
                'articles': {row['status']: row['n'] for row in articles}, 'license_holds': license_holds}

    def ingredient_coverage(self, food_ids):
        if not isinstance(food_ids, (list, tuple)) or any(not isinstance(fid, str) or not FOOD.fullmatch(fid)
                                                          for fid in food_ids):
            raise ValueError('Final FOOD IDs required')
        coverage = {fid: {'pending': 0, 'approved': 0} for fid in sorted(set(food_ids))}
        if not coverage:
            return coverage
        with self.connect() as conn:
            rows = conn.execute('''SELECT ingredient_id,status,count(*) AS n FROM
                (SELECT DISTINCT ON (knowledge_id) ingredient_id,status FROM knowledge_records
                 ORDER BY knowledge_id,seq DESC) current
                WHERE ingredient_id=ANY(%s) AND status IN ('pending','approved')
                GROUP BY ingredient_id,status''', (list(coverage),)).fetchall()
        for row in rows:
            coverage[row['ingredient_id']][row['status']] = row['n']
        return coverage

    def active_index(self):
        with self.connect() as conn:
            row = conn.execute("SELECT index_version,embedding_model,template_version,record_count "
                               "FROM knowledge_indexes WHERE status='active'").fetchone()
        return dict(row) if row else None

    def import_records(self, records):
        """Atomic file import: identical versions are skipped, changed ones fail."""
        prepared = [(record, validate_record(record)) for record in records]
        imported = 0
        with self.connect() as conn:
            for record, payload in prepared:
                inserted = conn.execute('''INSERT INTO knowledge_records
                    (knowledge_id,source_version,ingredient_id,payload,excerpt,source_url,rights,conditions,
                     nutrition_fact,evidence_quote,fetched_at,content_provider,authorization_ref,classification,
                     classification_reason,article_id,content_hash,published_at,source_name,title,license_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (knowledge_id,source_version) DO NOTHING RETURNING knowledge_id''',
                    (record['knowledge_id'], record['source_version'], record['ingredient_id'], _jsonb(payload),
                     record['excerpt'], record['source_url'], record['rights'], record['conditions'],
                     record.get('nutrition_fact'), record.get('evidence_quote'), record.get('fetched_at'),
                     record.get('content_provider'), record.get('authorization_ref'), record.get('classification'),
                     record.get('classification_reason'), record.get('article_id'), record.get('content_hash'),
                     record.get('published_at'), record.get('source_name'), record.get('title'),
                     record.get('license_status'))).fetchone()
                if inserted:
                    imported += 1
                else:
                    existing = conn.execute(
                        'SELECT payload FROM knowledge_records WHERE knowledge_id=%s AND source_version=%s',
                        (record['knowledge_id'], record['source_version'])).fetchone()
                    if existing is None or not same_imported_content(existing['payload'], payload):
                        raise ValueError('Immutable version changed; use a new source_version')
        return dict(imported=imported, unchanged=len(prepared)-imported, status='pending')

    def review(self, kid, version, *, reviewer, approved, note, confirm_third_party_license=False):
        if not isinstance(approved,bool) or not all(isinstance(x,str) and x.strip() for x in (reviewer,note)):
            raise ValueError('Reviewer, decision and source-check note required')
        with self.connect() as conn:
            current = conn.execute("SELECT payload,license_status FROM knowledge_records WHERE knowledge_id=%s "
                                   "AND source_version=%s AND status='pending' FOR UPDATE", (kid, version)).fetchone()
            if current is None:
                raise ValueError('Only an existing pending version can be reviewed')
            license_status = current['license_status'] or current['payload'].get('license_status')
            if approved and license_status == LICENSE_UNCONFIRMED and not confirm_third_party_license:
                raise ValueError('third-party license confirmation is required; use --confirm-third-party-license')
            approved_license = 'third_party_confirmed' if approved and license_status == LICENSE_UNCONFIRMED else license_status
            review_note = note
            if approved_license == 'third_party_confirmed':
                provider = current['payload'].get('content_provider')
                review_note = f'{note}；已確認第三方授權涵蓋轉載內容' + (f'（{provider}）' if provider else '')
            conn.execute('UPDATE knowledge_records SET status=%s,reviewer=%s,review_note=%s,reviewed_at=now(),'
                         'license_status=%s WHERE knowledge_id=%s AND source_version=%s AND status=\'pending\'',
                         ('approved' if approved else 'rejected', reviewer, review_note, approved_license, kid, version))
            row = {'status': 'approved' if approved else 'rejected'}
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
                                "ORDER BY knowledge_id,seq DESC) c WHERE status='approved' "
                                "AND (classification IS NULL OR classification='nutrition') "
                                "ORDER BY knowledge_id").fetchall()
            if not rows:
                raise ValueError('No approved knowledge records available for indexing')
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
