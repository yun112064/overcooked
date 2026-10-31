"""T17：部署前備份＋Alembic 閘門、正式設定檢查（R11、Spec 遷移限制）。

- 正式環境後端不在啟動時自動升級 schema（AUTO_MIGRATE=false）；啟動前確認資料庫已在 Alembic head，
  不符就拒絕啟動，避免新程式碼跑在舊 schema 上。
- 部署腳本順序固定：設定檢查 → 部署前備份 → 建置 → alembic upgrade → 啟動；任何一步失敗即停止，
  遷移失敗時提示用部署前備份還原，絕不自動 downgrade。
"""
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from db_support import TEST_DATABASE_URL, migrate_fresh, requires_db


@requires_db
class SchemaGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()
        cls.engine = create_engine(TEST_DATABASE_URL)

    def set_revision(self, revision):
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})

    def test_database_at_head_passes(self):
        from app.core.schema_gate import schema_status
        status = schema_status(TEST_DATABASE_URL)
        self.assertTrue(status["at_head"], status)
        self.assertEqual([status["current"]], status["heads"])

    def test_database_behind_head_is_refused(self):
        from app.core.schema_gate import main, schema_status
        head = schema_status(TEST_DATABASE_URL)["current"]
        self.addCleanup(self.set_revision, head)
        self.set_revision("f16133cc3730")  # baseline
        status = schema_status(TEST_DATABASE_URL)
        self.assertFalse(status["at_head"])
        self.assertEqual(main(TEST_DATABASE_URL), 1)


class FakeSteps:
    def __init__(self, fail=None):
        self.fail, self.log = fail, []

    def preflight(self):
        self.log.append("preflight")
        return ["ALLOWED_ORIGINS 不是 https"] if self.fail == "preflight" else []

    def backup(self):
        self.log.append("backup")
        if self.fail == "backup":
            raise RuntimeError("disk full")
        return Path("/var/backups/meal-planner/cloud/20261020T023000Z-predeploy")

    def run(self, args):
        self.log.append(" ".join(args))
        return 1 if self.fail and self.fail in " ".join(args) else 0


class DeployOrderTests(unittest.TestCase):
    def deploy(self, steps):
        from ops.deploy import deploy
        return deploy(preflight=steps.preflight, backup=steps.backup, run=steps.run, out=lambda *_: None)

    def test_backup_happens_before_migration_and_start(self):
        steps = FakeSteps()
        self.assertEqual(self.deploy(steps), 0)
        self.assertEqual(steps.log[:2], ["preflight", "backup"])
        joined = steps.log[2:]
        build = next(i for i, s in enumerate(joined) if " build" in s)
        upgrade = next(i for i, s in enumerate(joined) if "alembic" in s and "upgrade head" in s)
        up = next(i for i, s in enumerate(joined) if " up -d" in s)
        self.assertLess(build, upgrade)
        self.assertLess(upgrade, up)

    def test_failed_preflight_or_backup_stops_before_any_change(self):
        for fail in ("preflight", "backup"):
            steps = FakeSteps(fail)
            self.assertNotEqual(self.deploy(steps), 0)
            self.assertFalse([s for s in steps.log if "alembic" in s or " up -d" in s], fail)

    def test_failed_migration_never_downgrades_and_points_to_restore(self):
        from ops.deploy import deploy
        steps, messages = FakeSteps("upgrade"), []
        code = deploy(preflight=steps.preflight, backup=steps.backup, run=steps.run, out=messages.append)
        self.assertNotEqual(code, 0)
        self.assertFalse([s for s in steps.log if "downgrade" in s or " up -d" in s])
        self.assertIn("20261020T023000Z-predeploy", "\n".join(messages))
        self.assertIn("ops.restore", "\n".join(messages))


GOOD_CLOUD = {
    "PUBLIC_HOSTNAME": "meal.team-domain.test",
    "CLOUDFLARE_TUNNEL_TOKEN": "x" * 120,
    "ALLOWED_ORIGINS": "https://meal.team-domain.test",
    "COOKIE_SECURE": "true",
    "POSTGRES_PASSWORD": "q" * 32,
    "PROJECT_DATA_ROOT": "/var/lib/meal-planner/data",
    "PROJECT_RUNTIME_ROOT": "/var/lib/meal-planner/runtime",
    "PROJECT_BACKUP_ROOT": "/var/backups/meal-planner",
    "BACKUP_MIRROR_ROOT": "/mnt/offsite/meal-planner",
    "RECOGNITION_SERVICE_URL": "https://yolo.team-domain.test",
    "RECOGNITION_SERVICE_TOKEN": "r" * 40,
    "KNOWLEDGE_SERVICE_URL": "https://knowledge.team-domain.test",
    "KNOWLEDGE_SERVICE_TOKEN": "k" * 40,
    "TEAM_ACCESS_CLIENT_ID": "abc123.access",
    "TEAM_ACCESS_CLIENT_SECRET": "s" * 64,
    "OPENAI_API_KEY": "o" * 40,
    "INTERNAL_API_TOKEN": "n" * 40,
}


class PreflightTests(unittest.TestCase):
    def errors(self, **changes):
        from ops.preflight import check
        env = {**GOOD_CLOUD, **changes}
        env = {k: v for k, v in env.items() if v is not None}
        return check(env, side="cloud")

    def test_complete_production_settings_pass(self):
        self.assertEqual(self.errors(), ([], []))

    def test_each_unsafe_setting_is_reported(self):
        cases = {
            "CLOUDFLARE_TUNNEL_TOKEN": [None, "replace_with_tunnel_token"],
            "PUBLIC_HOSTNAME": [None, "localhost"],
            "ALLOWED_ORIGINS": ["http://meal.team-domain.test", "https://other.test"],
            "COOKIE_SECURE": ["false"],
            "POSTGRES_PASSWORD": ["short", "replace_with_random_password_xxxxxx"],
            "PROJECT_BACKUP_ROOT": ["/var/lib/meal-planner/data/backups", "../relative"],
            "BACKUP_MIRROR_ROOT": [None],
            "KNOWLEDGE_SERVICE_URL": ["http://knowledge.team-domain.test"],  # 未加密
            "KNOWLEDGE_SERVICE_TOKEN": [None, "short"],
            "RECOGNITION_SERVICE_TOKEN": ["placeholder-token-placeholder-token-xx"],
            "TEAM_ACCESS_CLIENT_SECRET": [None],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    errors, _ = self.errors(**{key: value})
                    self.assertTrue(any(key in e for e in errors), errors)

    def test_unconfigured_team_services_are_warnings_not_errors(self):
        errors, warnings = self.errors(RECOGNITION_SERVICE_URL="", KNOWLEDGE_SERVICE_URL="",
                                       TEAM_ACCESS_CLIENT_ID=None, TEAM_ACCESS_CLIENT_SECRET=None, OPENAI_API_KEY="",
                                       INTERNAL_API_TOKEN=None)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 4)
        self.assertTrue(any("INTERNAL_API_TOKEN" in w for w in warnings))
        errors, _ = self.errors(INTERNAL_API_TOKEN="short")
        self.assertTrue(any("INTERNAL_API_TOKEN" in e for e in errors))

    def test_errors_never_echo_secret_values(self):
        secret = "replace_me_" + "z" * 30
        errors, _ = self.errors(KNOWLEDGE_SERVICE_TOKEN=secret, CLOUDFLARE_TUNNEL_TOKEN=secret)
        self.assertTrue(errors)
        self.assertNotIn(secret, "\n".join(errors))

    def test_team_computer_settings(self):
        from ops.preflight import check
        good = {"TEAM_TUNNEL_TOKEN": "t" * 120, "KNOWLEDGE_TOKEN": "k" * 40, "INFERENCE_TOKEN": "i" * 40,
                "KNOWLEDGE_DB_PASSWORD": "p" * 32, "TEAM_BACKUP_ROOT": "D:/AIPE04期末專題_backups",
                "BACKUP_MIRROR_ROOT": "E:/offsite"}
        self.assertEqual(check(good, side="team")[0], [])
        errors, _ = check({**good, "KNOWLEDGE_TOKEN": "short", "TEAM_TUNNEL_TOKEN": ""}, side="team")
        self.assertEqual(sorted(e.split(" ")[0] for e in errors), ["KNOWLEDGE_TOKEN", "TEAM_TUNNEL_TOKEN"])
        # pre-review #6：團隊電腦只需雲端內部 API（https＋Bearer＋Access 服務權杖），不需要雲端 DATABASE_URL
        _, warnings = check(good, side="team")
        self.assertTrue(any("CLOUD_INTERNAL_URL" in w for w in warnings))
        internal = {**good, "CLOUD_INTERNAL_URL": "https://cloud-internal.team-domain.test",
                    "CLOUD_INTERNAL_TOKEN": "c" * 40, "CLOUD_ACCESS_CLIENT_ID": "id.access",
                    "CLOUD_ACCESS_CLIENT_SECRET": "s" * 64}
        self.assertEqual(check(internal, side="team"), ([], []))
        errors, _ = check({**internal, "CLOUD_INTERNAL_URL": "http://x.test", "CLOUD_ACCESS_CLIENT_SECRET": ""},
                          side="team")
        self.assertEqual(sorted(e.split(" ")[0] for e in errors), ["CLOUD_ACCESS_CLIENT_SECRET", "CLOUD_INTERNAL_URL"])
        errors, _ = check({**internal, "DATABASE_URL": "postgresql://u:p@cloud/db"}, side="team")
        self.assertTrue(any("DATABASE_URL" in e for e in errors))


def compose_config(files, env, project_dir=None):
    """以 docker compose config 取得合併後設定（不啟動任何容器）。"""
    import json
    import os
    import subprocess
    from db_support import ROOT
    cmd = ["docker", "compose"]
    if project_dir:
        cmd += ["--project-directory", str(project_dir)]
    for f in files:
        cmd += ["-f", str(ROOT / f)]
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "PROJECT_", "CLOUDFLARE_"))}
    proc = subprocess.run([*cmd, "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env={**full_env, **env}, timeout=120)
    return proc.returncode, (json.loads(proc.stdout) if proc.returncode == 0 else proc.stderr)


PROD_ENV = {"POSTGRES_PASSWORD": "placeholder-pw", "PUBLIC_HOSTNAME": "meal.team-domain.test",
            "CLOUDFLARE_TUNNEL_TOKEN": "placeholder-tunnel", "PROJECT_DATA_ROOT": "/var/lib/meal-planner/data",
            "PROJECT_RUNTIME_ROOT": "/var/lib/meal-planner/runtime", "PROJECT_BACKUP_ROOT": "/var/backups/meal-planner"}


class ComposeTests(unittest.TestCase):
    def test_production_publishes_nothing_and_goes_out_through_the_tunnel(self):
        code, cfg = compose_config(["compose.yaml", "compose.prod.yaml"], PROD_ENV)
        self.assertEqual(code, 0, cfg)
        services = cfg["services"]
        for name, svc in services.items():
            self.assertFalse(svc.get("ports"), f"{name} 不可對主機開埠")
        self.assertEqual(services["cloudflared"]["environment"]["TUNNEL_TOKEN"], "placeholder-tunnel")
        backend = services["backend"]["environment"]
        self.assertEqual((backend["AUTO_MIGRATE"], backend["COOKIE_SECURE"], backend["ALLOWED_ORIGINS"]),
                         ("false", "true", "https://meal.team-domain.test"))
        self.assertIn("TEAM_ACCESS_CLIENT_SECRET", services["worker"]["environment"])
        self.assertEqual(sorted(v["source"] for v in services["backend"]["volumes"]),
                         ["/var/backups/meal-planner", "/var/lib/meal-planner/data", "/var/lib/meal-planner/runtime"])

    def test_production_refuses_to_render_without_tunnel_token_or_roots(self):
        for missing in ("CLOUDFLARE_TUNNEL_TOKEN", "PROJECT_DATA_ROOT", "PUBLIC_HOSTNAME"):
            env = {k: v for k, v in PROD_ENV.items() if k != missing}
            code, err = compose_config(["compose.yaml", "compose.prod.yaml"], env)
            self.assertNotEqual(code, 0, missing)
            self.assertIn(missing, err)

    def test_team_computer_services_bind_only_to_loopback(self):
        code, cfg = compose_config(["deploy/team/compose.yaml"],
                                   {"KNOWLEDGE_DB_PASSWORD": "placeholder-pw", "KNOWLEDGE_TOKEN": "placeholder-token"})
        self.assertEqual(code, 0, cfg)
        ports = [p for svc in cfg["services"].values() for p in svc.get("ports", [])]
        self.assertTrue(ports)
        self.assertEqual({p["host_ip"] for p in ports}, {"127.0.0.1"})
        self.assertEqual(cfg["services"]["knowledge-rag"]["environment"]["KNOWLEDGE_TOKEN"], "placeholder-token")


if __name__ == "__main__":
    unittest.main()
