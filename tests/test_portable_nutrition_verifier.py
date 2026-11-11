"""Audit helper tests use synthetic SQLite and records, not real acceptance data."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scripts.verify_portable_nutrition import ALL_NUTRIENTS, RUNTIME_TABLES, compare_record, runtime_counts


def test_installer_dotenv_quotes_are_decoded_without_interpolation(tmp_path, monkeypatch):
    from scripts.verify_portable_nutrition import argument_parser, load_environment
    import os
    path = tmp_path / 'runtime.env'
    path.write_text('PORTABLE_TEST_PIN="value-${NOT_EXPANDED}"\n', encoding='utf-8')
    monkeypatch.setenv('PORTABLE_TEST_PIN', 'old')
    args = argument_parser().parse_args(['--env-file', str(path), '--require-empty-runtime'])
    load_environment(args)
    assert os.environ['PORTABLE_TEST_PIN'] == 'value-${NOT_EXPANDED}'
    assert args.require_empty_runtime


def test_empty_runtime_counts_preserve_source_receipts():
    with Session(create_engine('sqlite://')) as db:
        for table in RUNTIME_TABLES:
            db.execute(text(f'CREATE TABLE "{table}" (id INTEGER)'))
        db.execute(text('CREATE TABLE t04_cooking_receipts (id INTEGER)'))
        db.execute(text('INSERT INTO t04_cooking_receipts VALUES (1)'))
        assert not any(runtime_counts(db).values())
        db.execute(text('INSERT INTO users VALUES (1)'))
        assert runtime_counts(db)['users'] == 1


def test_parity_denies_changed_value_source_and_null():
    record = {'nutrition_per_100g': {key: None for key in ALL_NUTRIENTS},
              'source': {'nutrition_source_id': 'source1'}}
    record['nutrition_per_100g']['energy_kcal'] = 10
    output = SimpleNamespace(error=None, nutrients={key: None for key in ALL_NUTRIENTS},
        trace={'nutrition_source_id': 'source1', 'nutrition_policy_manifest_sha256': 'hash'})
    output.nutrients['energy_kcal'] = Decimal(16)
    compare_record(output, record, Decimal(160), 'hash')
    output.trace['nutrition_source_id'] = 'changed'
    with pytest.raises(ValueError, match='source_parity'):
        compare_record(output, record, Decimal(160), 'hash')
    output.trace['nutrition_source_id'] = 'source1'
    output.nutrients['energy_kcal'] = Decimal(17)
    with pytest.raises(ValueError, match='nutrient_parity'):
        compare_record(output, record, Decimal(160), 'hash')
    output.nutrients['energy_kcal'] = None
    with pytest.raises(ValueError, match='null_parity'):
        compare_record(output, record, Decimal(160), 'hash')
