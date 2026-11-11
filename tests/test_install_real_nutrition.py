"""Pure installer safety/conversion checks; no PostgreSQL or synthetic acceptance."""
import base64
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import Column, DateTime, JSON, LargeBinary, MetaData, Numeric, String, Table

PATH = Path(__file__).resolve().parents[1] / 'scripts/install_real_nutrition.py'
spec = importlib.util.spec_from_file_location('portable_installer_tests', PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_binary_decimals_dates_and_json_keep_source_semantics():
    evidence = {'reported': None, 'zero': 0, 'original': '0.00'}
    assert installer.convert_value(JSON(), evidence) is evidence
    assert installer.convert_value(Numeric(24, 12), '0.00') == Decimal('0.00')
    assert installer.convert_value(LargeBinary(), {'__bytes_base64__': 'AAEC'}) == b'\x00\x01\x02'
    assert installer.convert_value(DateTime(timezone=True), '2026-11-11T00:00:00Z').tzinfo == timezone.utc


def test_content_hash_catches_changed_source_and_normalizes_sql_types():
    table = Table('source', MetaData(), Column('id', String), Column('value', Numeric(24, 12)),
                  Column('when', DateTime(timezone=True)), Column('raw', LargeBinary), Column('json', JSON))
    exported = {'id': 'real-id', 'value': '1.20', 'when': '2026-11-11T08:00:00+08:00',
                'raw': {'__bytes_base64__': base64.b64encode(b'archive').decode()}, 'json': {'unknown': None}}
    stored = {**exported, 'value': Decimal('1.200000000000'),
              'when': datetime(2026, 11, 11, tzinfo=timezone.utc), 'raw': b'archive'}
    assert installer.canonical_rows_digest(table, [exported]) == installer.canonical_rows_digest(table, [stored])
    assert installer.canonical_rows_digest(table, [exported]) != installer.canonical_rows_digest(table, [{**stored, 'json': {'unknown': 0}}])


@pytest.mark.parametrize('existing', [[True], [False, True]])
def test_existing_database_or_role_is_rejected_without_create(existing):
    class Connection:
        values = iter(existing)
        def scalar(self, statement, values):
            return next(self.values)
        def exec_driver_sql(self, sql):
            pytest.fail('Existing targets must not be mutated')
    with pytest.raises(installer.InstallError, match='already exists'):
        installer.create_new_database(Connection(), 'new_database', 'new_role')


@pytest.mark.parametrize('name', ['postgres;DROP DATABASE postgres', '', 'x' * 64, 'has space', 'name"quote'])
def test_database_identifier_rejected(name):
    with pytest.raises(installer.InstallError):
        installer.database_name(name)


def test_bundle_hash_and_forbidden_table_fail_before_database(tmp_path):
    counts = {name: 0 for name in installer.CORE_TABLES}
    payload = {'tables': {name: [] for name in counts}}
    data = tmp_path / 'data.json.gz'
    data.write_bytes(gzip.compress(json.dumps(payload).encode()))
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'status': 'approved_for_release', 'expected_schema_head': installer.SCHEMA_HEAD}))
    value = {'schema_version': 'portable-nutrition-v1', 'schema_head': installer.SCHEMA_HEAD,
             'synthetic': False, 'data_file': data.name, 'data_sha256': installer.digest(data),
             'policy_file': policy.name, 'policy_sha256': installer.digest(policy), 'tables': counts}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(value))
    assert installer.load_bundle(tmp_path)[1] == payload['tables']
    value['tables']['users'] = 0
    manifest.write_text(json.dumps(value))
    with pytest.raises(installer.InstallError, match='whitelist'):
        installer.load_bundle(tmp_path)
    del value['tables']['users']
    value['data_sha256'] = '0' * 64
    manifest.write_text(json.dumps(value))
    with pytest.raises(installer.InstallError, match='hash mismatch'):
        installer.load_bundle(tmp_path)
