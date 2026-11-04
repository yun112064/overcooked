"""V3-only database guards and content fingerprints; no credentials in reports."""
import hashlib
import json
from sqlalchemy import text

RUNTIME_DB = 'meal_planner_1102_v3'
TEST_DB = 'meal_planner_1102_v3_t04_test'
HEAD = 'd1103d04b001'


def guard(engine, *, test=False):
    allowed = TEST_DB if test else RUNTIME_DB
    url = engine.url
    if (url.get_backend_name() != 'postgresql' or url.database != allowed
            or url.host != '127.0.0.1' or url.port != 55435):
        raise ValueError('Only the dedicated V3 target is authorized: ' + allowed)
    with engine.connect() as connection:
        if connection.scalar(text('SELECT current_database()')) != allowed:
            raise ValueError('Database identity mismatch')


def snapshot(connection, *, exclude=()):
    tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
    counts, hashes = {}, {}
    for table in tables:
        if table in exclude:
            continue
        counts[table] = connection.scalar(text('SELECT count(*) FROM "' + table + '"'))
        rows = connection.execute(text('SELECT to_jsonb(t) FROM "' + table + '" t ORDER BY to_jsonb(t)::text')).scalars().all()
        hashes[table] = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'table_counts': counts, 'table_digests': hashes}
