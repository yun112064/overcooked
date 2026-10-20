"""T17：每日備份、保留與還原演練（R11、AC27）。

以真實 PostgreSQL（測試庫所在的 Docker 容器，經 docker exec 執行 pg_dump／pg_restore／psql）
建立備份、驗證校驗碼、還原到臨時資料庫 <測試庫>_restore 後檢查家庭與已採用餐食可讀，
並從知識來源快照重建知識庫。臨時資料庫在每個測試後刪除；所有資料皆為合成資料。
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from db_support import ROOT, TEST_DATABASE_URL, make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all

NOW = datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc)


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/").split("?")[0]


def test_pg():
    """測試庫所在容器：BACKUP_PG_CONTAINER，未設定則以測試庫埠號找出發佈該埠的容器。"""
    from ops.pg import PgTools, find_container_for_port
    container = os.getenv("BACKUP_PG_CONTAINER") or find_container_for_port(urlsplit(TEST_DATABASE_URL).port or 5432)
    if not container:
        raise RuntimeError("找不到測試資料庫容器；請設定 BACKUP_PG_CONTAINER")
    return PgTools(container=container)


@requires_db
class BackupRestoreTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()
        cls.pg = test_pg()
        cls.db = _db_name(TEST_DATABASE_URL)
        cls.scratch = f"{cls.db}_restore"

    def setUp(self):
        truncate_all()
        seed_foods()
        self.tmp = Path(tempfile.mkdtemp(prefix="t17-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.pg.drop_db, self.scratch)
        self.pg.drop_db(self.scratch)
        self.backup_root = self.tmp / "backups"
        self.data_root = self.tmp / "data"
        (self.data_root / "uploads").mkdir(parents=True)
        (self.data_root / "uploads" / "photo.jpg").write_bytes(b"synthetic-photo")
        (self.data_root / "logs").mkdir()
        (self.data_root / "logs" / "backend.jsonl").write_text("{}\n", encoding="utf-8")

    def seed_history(self):
        from app.services import meal_history
        client = make_client()
        client.post("/auth/register", json={"email": "owner@example.test", "password": "correct-horse-42"})
        household_id = client.post("/household", json={"name": "還原測試家庭"}).json()["id"]
        with session_factory()() as db, db.begin():
            for day, dish in ((date(2026, 8, 19), "番茄炒蛋"), (date(2026, 8, 20), "清炒高麗菜")):
                meal_history.record_adopted(db, household_id, day, "dinner", mode="database_menu",
                                            dishes=[{"standard_dish_id": f"DISH-{dish}", "dish_name": dish,
                                                     "aliases": []}])
        return household_id

    def cloud_backup(self, now=NOW, **kw):
        from ops.backup import create_backup
        return create_backup("cloud", pg=self.pg, database=self.db, backup_root=self.backup_root,
                             code_root=ROOT, data_root=self.data_root, now=now, **kw)


class CloudBackupTests(BackupRestoreTestCase):
    def test_backup_records_schema_revision_counts_and_checksums(self):
        from ops.backup import verify_backup
        self.seed_history()
        path = self.cloud_backup()
        self.assertEqual(path, self.backup_root / "cloud" / "20261020T023000Z")
        manifest = verify_backup(path)
        revision = self.pg.query(self.db, "SELECT version_num FROM alembic_version")[0][0]
        self.assertEqual(manifest["alembic_revision"], revision)
        self.assertEqual((manifest["counts"]["households"], manifest["counts"]["adopted_meal_plans"]), (1, 2))
        self.assertEqual(set(manifest["files"]), {"database.dump", "data_root.tar.gz", "snapshot.tar.gz"})
        self.assertIn("config/model_pricing.json", manifest["snapshot_paths"])
        self.assertIn("nutrition_target", manifest["snapshot_paths"])
        self.assertFalse(list(self.backup_root.rglob("*.partial")))

    def test_tampered_backup_is_rejected_before_restore(self):
        from ops.backup import BackupError
        from ops.restore import restore_backup
        path = self.cloud_backup()
        with open(path / "database.dump", "ab") as fh:
            fh.write(b"x")
        with self.assertRaises(BackupError):
            restore_backup(path, pg=self.pg, target_database=self.scratch)
        self.assertFalse(self.pg.database_exists(self.scratch))

    def test_restore_into_scratch_database_makes_households_and_meals_readable(self):
        from ops.restore import check_cloud_restore, restore_backup
        self.seed_history()
        path = self.cloud_backup()
        truncate_all()  # 模擬原庫資料遺失：還原只依賴備份
        data_target = self.tmp / "restored-data"
        manifest = restore_backup(path, pg=self.pg, target_database=self.scratch, data_target=data_target)
        report = check_cloud_restore(self.pg, self.scratch, manifest)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual((report["counts"]["households"], report["counts"]["adopted_meal_plans"]), (1, 2))
        self.assertEqual(report["adopted_dates"], ["2026-08-19", "2026-08-20"])
        self.assertEqual(report["adopted_dishes"], ["番茄炒蛋", "清炒高麗菜"])
        self.assertEqual((data_target / "uploads" / "photo.jpg").read_bytes(), b"synthetic-photo")
        self.assertFalse((data_target / "logs").exists())  # Log 不在業務資料備份範圍

    def test_restore_refuses_a_database_that_already_has_tables(self):
        from ops.restore import RestoreError, restore_backup
        path = self.cloud_backup()
        with self.assertRaises(RestoreError) as ctx:
            restore_backup(path, pg=self.pg, target_database=self.db)  # 絕不覆寫現有資料庫
        self.assertEqual(ctx.exception.code, "TARGET_NOT_EMPTY")

    def test_check_reports_count_mismatch(self):
        from ops.restore import check_cloud_restore, restore_backup
        self.seed_history()
        manifest = restore_backup(self.cloud_backup(), pg=self.pg, target_database=self.scratch)
        self.pg.query(self.scratch, "DELETE FROM adopted_meal_plans WHERE meal_date = '2026-08-20'")
        report = check_cloud_restore(self.pg, self.scratch, manifest)
        self.assertFalse(report["ok"])
        self.assertIn("adopted_meal_plans", " ".join(report["problems"]))


class RetentionTests(BackupRestoreTestCase):
    def test_backups_older_than_seven_days_are_pruned_but_newest_is_kept(self):
        from ops.backup import prune_backups
        old = self.cloud_backup(now=NOW - timedelta(days=8))
        edge = self.cloud_backup(now=NOW - timedelta(days=6, hours=23))
        new = self.cloud_backup(now=NOW)
        partial = self.backup_root / "cloud" / "20261001T000000Z.partial"
        partial.mkdir()
        os.utime(partial, ((NOW - timedelta(days=2)).timestamp(),) * 2)
        removed = prune_backups(self.backup_root / "cloud", keep_days=7, now=NOW)
        self.assertEqual(sorted(removed), sorted([old, partial]))
        self.assertTrue(edge.exists() and new.exists())

    def test_only_backup_is_never_pruned(self):
        from ops.backup import prune_backups
        only = self.cloud_backup(now=NOW - timedelta(days=30))
        self.assertEqual(prune_backups(self.backup_root / "cloud", keep_days=7, now=NOW), [])
        self.assertTrue(only.exists())

    def test_mirror_copy_is_verified_in_independent_location(self):
        from ops.backup import mirror_backup, verify_backup
        path = self.cloud_backup()
        copy = mirror_backup(path, self.tmp / "offsite")
        self.assertEqual(copy, self.tmp / "offsite" / "cloud" / path.name)
        self.assertEqual(verify_backup(copy)["files"], verify_backup(path)["files"])


class KnowledgeBackupTests(BackupRestoreTestCase):
    """團隊電腦知識庫：還原可用，或只用來源快照重建後再建索引（索引可重建，來源版本保留）。"""

    def setUp(self):
        super().setUp()
        from knowledge_support import FakeEmbedder, fresh_knowledge_db, knowledge_url, record
        from knowledge_rag.store import KnowledgeStore
        self.knowledge_db = _db_name(knowledge_url())
        fresh_knowledge_db()
        self.store = KnowledgeStore(knowledge_url())
        self.store.import_record(record("K1", "FOOD000867", "高麗菜含維生素 C（合成）\\不是真實來源"))
        self.store.import_record(record("K2", "FOOD000678", "胡蘿蔔含纖維（合成）"))
        self.store.import_record(record("K3", "FOOD000585", "青江菜含鈣（合成），未審核"))
        for kid in ("K1", "K2"):
            self.store.review(kid, "v1", reviewer="審核者", approved=True, note="合成資料審核")
        self.embedder = FakeEmbedder()
        self.store.build_index("idx-1", self.embedder.model, lambda texts: self.embedder.embed(texts)[0])
        self.rebuilt = f"{self.db}_rebuild"
        self.pg.drop_db(self.rebuilt)
        self.addCleanup(self.pg.drop_db, self.rebuilt)

    def knowledge_backup(self):
        from ops.backup import create_backup
        return create_backup("knowledge", pg=self.pg, database=self.knowledge_db, backup_root=self.backup_root,
                             code_root=ROOT, now=NOW)

    def test_knowledge_backup_contains_dump_sources_and_counts(self):
        from ops.backup import verify_backup
        manifest = verify_backup(self.knowledge_backup())
        self.assertEqual(set(manifest["files"]), {"database.dump", "knowledge_sources.jsonl", "snapshot.tar.gz"})
        self.assertEqual((manifest["counts"]["knowledge_records"], manifest["counts"]["approved_records"]), (3, 2))
        self.assertEqual(manifest["counts"]["active_index"],
                         {"index_version": "idx-1", "embedding_model": "text-embedding-3-small",
                          "record_count": 2, "embeddings": 2})

    def test_restored_knowledge_database_serves_search(self):
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import fake_vector
        from ops.restore import check_knowledge_restore, restore_backup
        manifest = restore_backup(self.knowledge_backup(), pg=self.pg, target_database=self.scratch)
        report = check_knowledge_restore(self.pg, self.scratch, manifest)
        self.assertTrue(report["ok"], report["problems"])
        restored = KnowledgeStore(_scratch_url(self.scratch))
        result = restored.search(["FOOD000678"], fake_vector("纖維"), "text-embedding-3-small")
        self.assertEqual([r["knowledge_id"] for r in result["records"]], ["K2"])

    def test_knowledge_is_rebuildable_from_source_snapshot_alone(self):
        from knowledge_rag.store import KnowledgeStore
        from ops.restore import rebuild_knowledge_from_sources
        report = rebuild_knowledge_from_sources(self.knowledge_backup(), pg=self.pg, target_database=self.rebuilt)
        self.assertTrue(report["ok"], report["problems"])
        self.assertIsNone(report["counts"]["active_index"])  # 向量索引不在來源快照內，需重建
        self.assertEqual(report["index_to_rebuild"]["index_version"], "idx-1")
        rows = self.pg.query(self.rebuilt, "SELECT knowledge_id, status, reviewer, excerpt FROM knowledge_records "
                                           "ORDER BY knowledge_id")
        self.assertEqual([(r[0], r[1], r[2]) for r in rows],
                         [("K1", "approved", "審核者"), ("K2", "approved", "審核者"), ("K3", "pending", "")])
        self.assertIn("\\不是真實來源", rows[0][3])  # 反斜線等特殊字元原樣保留
        rebuilt = KnowledgeStore(_scratch_url(self.rebuilt))
        result = rebuilt.build_index("idx-rebuilt", self.embedder.model, lambda t: self.embedder.embed(t)[0])
        self.assertEqual(result["records"], 2)  # 只有已審核的知識進入重建後的索引


def _scratch_url(database: str) -> str:
    from knowledge_support import _libpq
    base = _libpq(TEST_DATABASE_URL)
    parts = urlsplit(base)
    return parts._replace(path=f"/{database}").geturl()


if __name__ == "__main__":
    unittest.main()
