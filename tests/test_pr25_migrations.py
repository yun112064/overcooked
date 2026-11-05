"""Merge both published schema lineages without losing adopted snapshots or retry identity."""
import json
from pathlib import Path
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db
from app.services.idempotency import request_hash


@requires_db
class PublishedLineageTests(unittest.TestCase):
    def test_main_adoption_survives_upgrade_and_each_branch_downgrade(self):
        migrate_fresh()
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.attributes.update(database_url=TEST_DATABASE_URL, configure_logger=False)
        engine = create_engine(TEST_DATABASE_URL)
        self.addCleanup(engine.dispose)
        command.downgrade(cfg, 'base')
        command.upgrade(cfg, 'c4d8e2a6f1b3')
        household, request, plan = (uuid.uuid4() for _ in range(3))
        snapshot = {'selected': [{'recipe_id': 'R1'}], 'nutrition': {'totals': {'protein_g': '31/2'}}}
        with engine.begin() as db:
            db.execute(text("INSERT INTO households (id,name,created_at) VALUES (:id,'synthetic',now())"),
                       {'id': household})
            db.execute(text("INSERT INTO meal_requests (id,household_id,status,version,payload,created_at,updated_at) "
                            "VALUES (:id,:household,'confirmed',1,'{}',now(),now())"),
                       {'id': request, 'household': household})
            db.execute(text("INSERT INTO adopted_meal_plans "
                            "(id,household_id,meal_date,meal_type,mode,status,version,dishes,adopted_at,"
                            "meal_request_id,request_version,idempotency_key,snapshot) "
                            "VALUES (:id,:household,'2026-11-05','dinner','database_menu','adopted',1,'[]',now(),"
                            ":request,1,'published-main-key',CAST(:snapshot AS jsonb))"),
                       {'id': plan, 'household': household, 'request': request, 'snapshot': json.dumps(snapshot)})

        def verify(with_hash):
            with engine.connect() as db:
                row = db.execute(text('SELECT * FROM adopted_meal_plans WHERE id=:id'), {'id': plan}).mappings().one()
                self.assertEqual(row['snapshot'], snapshot)
                self.assertEqual(row['idempotency_key'], 'published-main-key')
                if with_hash:
                    self.assertEqual(row['request_hash'], request_hash('adopt-preview', request, 1, ['R1']))

        command.upgrade(cfg, 'head')
        verify(True)
        command.downgrade(cfg, 'c4d8e2a6f1b3')
        verify(False)
        self.assertTrue(inspect(engine).has_table('meal_menu_rejections'))
        command.upgrade(cfg, 'head')
        verify(True)
        command.downgrade(cfg, 'd1104t01vol1')
        command.downgrade(cfg, 'c4d8e2a6f1b3-1')
        verify(True)
        self.assertTrue(inspect(engine).has_table('generation_results'))
        self.assertFalse(inspect(engine).has_table('meal_menu_rejections'))
        command.upgrade(cfg, 'head')
        verify(True)
