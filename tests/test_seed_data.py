"""資料種子：開發資料庫整理好的推估、營養知識、價格快照匯出成 data/seed/*.json，隊友以一個指令補齊。

匯出（scripts/export_seed_data.py，唯讀、不 import app）→ 匯入（python -m app.jobs.import_seed_data）往返；
匯入冪等、可 dry-run、檢查 alembic 版本；知識只含現行版本 approved＋nutrition，不含向量；絕不含個人資料表。
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, text

from db_support import ROOT, TEST_DATABASE_URL, migrate_fresh, requires_db, truncate_all

SEED_FILES = {"conversion_factor_estimates.json", "knowledge_records.json", "knowledge_source_articles.json",
              "price_collection_runs.json", "price_quote_snapshots.json", "manifest.json"}
PERSONAL_TABLES = ("households", "members", "meal_requests", "generation_results", "adopted_meal_plans",
                   "meal_result_decisions", "background_jobs", "usage_reservations", "users", "accounts")


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_seed_data", ROOT / "scripts" / "export_seed_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _libpq(url):
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed_app_rows():
    engine = create_engine(TEST_DATABASE_URL)
    try:
        _insert_app_rows(engine)
    finally:
        engine.dispose()


def _insert_app_rows(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO conversion_factor_estimates (id, kind, food_id, factor_key, food_name, yield_factor, "
            "energy_retention, protein_retention, grams_per_unit, tfda_sample_id, nutrient_per_100g, source, status, "
            "model, prompt_version, schema_version, input_snapshot, assumptions, reviewer, review_note, reviewed_at, "
            "created_at) VALUES "
            "('00000000-0000-0000-0000-000000000001', 'cooking', 'FOOD000867', '炒', '高麗菜', 0.8500, 0.9500, 0.9000, "
            " NULL, NULL, NULL, 'llm_estimated', 'llm_estimated', 'm', 'p1', 's1', '{\"a\": 1}', '[\"假設一\"]', "
            " NULL, NULL, NULL, '2026-10-20T10:00:00+08:00'),"
            "('00000000-0000-0000-0000-000000000002', 'unit', 'FOOD001566', '顆', '雞蛋', NULL, NULL, NULL, 55.0000, "
            " NULL, NULL, 'human', 'approved', NULL, NULL, NULL, '{}', '[]', '阿明', '人工核對', "
            " '2026-10-21T09:00:00+08:00', '2026-10-21T08:00:00+08:00'),"
            "('00000000-0000-0000-0000-000000000003', 'tfda_mapping', 'FOOD001016', 'tfda', '番茄', NULL, NULL, NULL, "
            " NULL, 'J0101', NULL, 'human', 'overridden', NULL, NULL, NULL, '{}', '[]', '小華', '人工對照', "
            " '2026-10-21T09:00:00+08:00', '2026-10-21T08:30:00+08:00'),"
            "('00000000-0000-0000-0000-000000000004', 'nutrient_fill', 'J0101', 'sodium_mg', '番茄', NULL, NULL, NULL, "
            " NULL, NULL, 12.3400, 'llm_estimated', 'llm_estimated', 'm', 'p2', 's2', '{}', '[]', NULL, NULL, NULL, "
            " '2026-10-21T10:00:00+08:00')"))
        for run_id, run_key, provider in (("run-px-1", "pxmart:2026-10-20", "pxmart"),
                                          ("run-uni-1", "uniprosperity:2026-10-20", "uniprosperity")):
            conn.execute(text(
                "INSERT INTO price_collection_runs (id, run_key, provider, trigger_type, scheduled_for, started_at, "
                "finished_at, status, ingredient_total, success_count, status_counts, missing_reasons, "
                "mapping_failure_rate) VALUES (:id, :key, :p, 'manual', '2026-10-20T06:00:00+08:00', "
                "'2026-10-20T06:00:01+08:00', '2026-10-20T06:10:00+08:00', 'succeeded', 2, 2, '{\"ok\": 2}', '{}', "
                "0.125000)"), {"id": run_id, "key": run_key, "p": provider})
            for name, price in (("高麗菜", "45.0000"), ("雞蛋", "89.5000")):
                conn.execute(text(
                    "INSERT INTO price_quote_snapshots (run_id, provider, canonical_name, status, queried_at, "
                    "product_id, product_name, price, currency, package_quantity, package_unit, source_url, "
                    "quote_payload) VALUES (:r, :p, :n, 'ok', '2026-10-20T06:05:00+08:00', 'P1', :n, :price, 'TWD', "
                    "1.0000, '份', 'https://example.invalid/p', '{\"raw\": [1, 2]}')"),
                    {"r": run_id, "p": provider, "n": name, "price": price})


def _knowledge(conn, kid, version, status, classification, ingredient="FOOD000867", article="A1"):
    reviewed = status != "pending"
    conn.execute(
        "INSERT INTO knowledge_records (knowledge_id, source_version, ingredient_id, excerpt, source_url, rights, "
        "conditions, status, reviewer, reviewed_at, review_note, source_name, title, article_id, classification, "
        "nutrition_fact, evidence_quote) VALUES (%s, %s, %s, '高麗菜含膳食纖維。', 'https://example.invalid/a', "
        "'口頭授權', '一般成人', %s, %s, %s, %s, 'healthnews', '標題', %s, %s, '含膳食纖維', '含膳食纖維')",
        (kid, version, ingredient, status, "審核者" if reviewed else None,
         "2026-10-21T10:00:00+08:00" if reviewed else None, "核對" if reviewed else None, article, classification))


def _seed_knowledge_rows(url):
    with psycopg.connect(url, autocommit=True) as conn:
        _knowledge(conn, "K-ok", "v1", "approved", "nutrition", article="A1")
        _knowledge(conn, "K-not", "v1", "rejected", "not_nutrition", article="A2")
        _knowledge(conn, "K-pending", "v1", "pending", "nutrition", article="A3")
        _knowledge(conn, "K-superseded", "v1", "approved", "nutrition", article="A4")
        _knowledge(conn, "K-superseded", "v2", "pending", "nutrition", article="A4")
        for article in ("A1", "A2", "A3", "A4"):
            conn.execute(
                "INSERT INTO source_articles (source_name, article_id, url, title, status, matched_food_ids) "
                "VALUES ('healthnews', %s, 'https://example.invalid/a', '標題', 'matched', ARRAY['FOOD000867'])",
                (article,))


def _app_snapshot():
    with psycopg.connect(_libpq(TEST_DATABASE_URL)) as conn:
        return {
            "estimates": conn.execute("SELECT * FROM conversion_factor_estimates ORDER BY id").fetchall(),
            "runs": conn.execute("SELECT * FROM price_collection_runs ORDER BY run_key").fetchall(),
            "snapshots": conn.execute(
                "SELECT r.run_key, s.provider, s.canonical_name, s.status, s.queried_at, s.price, s.quote_payload "
                "FROM price_quote_snapshots s JOIN price_collection_runs r ON r.id = s.run_id ORDER BY 1, 3").fetchall(),
        }


def _knowledge_snapshot(url):
    with psycopg.connect(url) as conn:
        return {
            "records": conn.execute(
                "SELECT knowledge_id, source_version, status, classification, reviewer, nutrition_fact, article_id "
                "FROM knowledge_records ORDER BY 1, 2").fetchall(),
            "articles": conn.execute("SELECT * FROM source_articles ORDER BY 1, 2").fetchall(),
        }


@requires_db
class SeedDataRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()
        from knowledge_support import fresh_knowledge_db
        cls.fresh_knowledge_db = staticmethod(fresh_knowledge_db)

    def setUp(self):
        truncate_all()
        self.kn_url = self.fresh_knowledge_db()
        _seed_app_rows()
        _seed_knowledge_rows(self.kn_url)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seed_dir = Path(self.tmp.name) / "seed"
        self.exporter = _load_exporter()
        self.manifest = self.exporter.export(_libpq(TEST_DATABASE_URL), self.kn_url, self.seed_dir)

    def _import(self, **kwargs):
        from app.jobs.import_seed_data import run
        return run(self.seed_dir, app_url=TEST_DATABASE_URL, knowledge_url=self.kn_url, **kwargs)

    def _read(self, name):
        return json.loads((self.seed_dir / name).read_text(encoding="utf-8"))

    def test_export_writes_only_seed_files_with_manifest_counts(self):
        self.assertEqual({p.name for p in self.seed_dir.iterdir()}, SEED_FILES)
        manifest = self._read("manifest.json")
        self.assertEqual(manifest["alembic_version"], "d4e8a2c7f913")
        self.assertEqual(manifest["files"]["conversion_factor_estimates.json"]["count"], 4)
        self.assertEqual(manifest["files"]["price_quote_snapshots.json"]["count"], 4)
        self.assertEqual(manifest["files"]["knowledge_records.json"]["count"], 1)
        self.assertIn("口頭授權", json.dumps(manifest, ensure_ascii=False))
        estimates = self._read("conversion_factor_estimates.json")
        self.assertEqual({e["kind"] for e in estimates}, {"cooking", "unit", "tfda_mapping", "nutrient_fill"})
        egg = next(e for e in estimates if e["kind"] == "unit")
        self.assertEqual(egg["grams_per_unit"], "55.0000")  # Decimal 轉字串，不失精度
        self.assertEqual(egg["reviewer"], "阿明")

    def test_knowledge_only_current_approved_nutrition_without_embeddings(self):
        records = self._read("knowledge_records.json")
        self.assertEqual([(r["knowledge_id"], r["source_version"]) for r in records], [("K-ok", "v1")])
        self.assertNotIn("embedding", json.dumps(records))
        self.assertNotIn("seq", records[0])
        articles = self._read("knowledge_source_articles.json")
        self.assertEqual([a["article_id"] for a in articles], ["A1"])

    def test_export_is_stable(self):
        again = Path(self.tmp.name) / "again"
        self.exporter.export(_libpq(TEST_DATABASE_URL), self.kn_url, again)
        for name in SEED_FILES - {"manifest.json"}:
            self.assertEqual((again / name).read_bytes(), (self.seed_dir / name).read_bytes(), name)

    def test_exporter_never_touches_personal_tables(self):
        source = (ROOT / "scripts" / "export_seed_data.py").read_text(encoding="utf-8")
        for table in PERSONAL_TABLES:
            self.assertNotRegex(source, rf"\b{table}\b", table)
        dumped = "".join((self.seed_dir / n).read_text(encoding="utf-8") for n in SEED_FILES)
        for table in PERSONAL_TABLES[:-2]:
            self.assertNotIn(table, dumped)

    def test_round_trip_into_empty_databases_then_idempotent(self):
        app_before = _app_snapshot()
        truncate_all()
        self.kn_url = self.fresh_knowledge_db()

        report = self._import()
        self.assertEqual(report["conversion_factor_estimates"]["inserted"], 4)
        self.assertEqual(report["price_quote_snapshots"]["inserted"], 4)
        self.assertEqual(report["knowledge_records"]["inserted"], 1)
        self.assertIn("index build", report["next_step"])
        self.assertEqual(_app_snapshot(), app_before)
        knowledge_after = _knowledge_snapshot(self.kn_url)
        self.assertEqual([r[:2] for r in knowledge_after["records"]], [("K-ok", "v1")])
        self.assertEqual(knowledge_after["records"][0][2:4], ("approved", "nutrition"))

        again = self._import()
        self.assertEqual(again["conversion_factor_estimates"]["inserted"], 0)
        self.assertEqual(again["price_collection_runs"]["inserted"], 0)
        self.assertEqual(again["price_quote_snapshots"]["inserted"], 0)
        self.assertEqual(again["knowledge_records"]["inserted"], 0)
        self.assertEqual(_app_snapshot(), app_before)
        self.assertEqual(_knowledge_snapshot(self.kn_url), knowledge_after)

    def test_price_snapshots_dedupe_by_run_key_when_run_id_differs(self):
        with psycopg.connect(_libpq(TEST_DATABASE_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM price_quote_snapshots WHERE run_id = 'run-px-1' AND canonical_name = '雞蛋'")
            conn.execute("DELETE FROM price_quote_snapshots WHERE run_id = 'run-uni-1'")
            conn.execute("UPDATE price_collection_runs SET id = 'local-uni' WHERE id = 'run-uni-1'")
        report = self._import()
        self.assertEqual(report["price_collection_runs"]["inserted"], 0)
        self.assertEqual(report["price_quote_snapshots"]["inserted"], 3)
        self.assertEqual(len(_app_snapshot()["snapshots"]), 4)
        with psycopg.connect(_libpq(TEST_DATABASE_URL)) as conn:
            self.assertEqual(conn.execute("SELECT DISTINCT run_id FROM price_quote_snapshots WHERE provider = "
                                          "'uniprosperity'").fetchall(), [("local-uni",)])

    def test_dry_run_writes_nothing(self):
        truncate_all()
        self.kn_url = self.fresh_knowledge_db()
        report = self._import(dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["conversion_factor_estimates"]["inserted"], 4)
        self.assertEqual(_app_snapshot()["estimates"], [])
        self.assertEqual(_knowledge_snapshot(self.kn_url)["records"], [])

    def test_rejects_seed_from_unknown_alembic_revision(self):
        manifest = self._read("manifest.json")
        manifest["alembic_version"] = "ffffffffffff"
        (self.seed_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            self._import()
        self.assertIn("alembic", str(caught.exception))
        self.assertEqual(_app_snapshot()["estimates"][0][0].int, 1)  # 原資料未被動到


if __name__ == "__main__":
    unittest.main()
