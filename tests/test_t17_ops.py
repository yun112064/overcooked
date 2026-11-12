"""T17 維運指令的純邏輯（不需資料庫）：設定檢查、部署順序、秘密遮蔽、保留期、快速通道網址。"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc)
TOKEN = "t" * 40


def cloud_env(**overrides):
    env = {"CLOUDFLARE_TUNNEL_TOKEN": "c" * 64, "PUBLIC_HOSTNAME": "meal.team-domain.tw",
           "ALLOWED_ORIGINS": "https://meal.team-domain.tw", "COOKIE_SECURE": "true",
           "POSTGRES_PASSWORD": "p" * 24, "DATABASE_URL": "postgresql+psycopg://app:secret@db:5432/meal_prod",
           "PROJECT_DATA_ROOT": "/srv/meal/data", "PROJECT_RUNTIME_ROOT": "/srv/meal/runtime",
           "PROJECT_BACKUP_ROOT": "/srv/meal/backups", "BACKUP_MIRROR_ROOT": "/mnt/offsite/meal",
           "OPENAI_API_KEY": "sk-" + "k" * 40, "INTERNAL_API_TOKEN": TOKEN,
           "RECOGNITION_SERVICE_URL": "https://yolo.team-domain.tw", "RECOGNITION_SERVICE_TOKEN": TOKEN,
           "KNOWLEDGE_SERVICE_URL": "https://knowledge.team-domain.tw", "KNOWLEDGE_SERVICE_TOKEN": TOKEN,
           "ASSISTANT_OPENAI_SERVICE_URL": "https://assistant.team-domain.tw", "ASSISTANT_SERVICE_TOKEN": TOKEN,
           "TEAM_ACCESS_CLIENT_ID": "id-12345678", "TEAM_ACCESS_CLIENT_SECRET": "s" * 40}
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def team_env(**overrides):
    env = {"TEAM_TUNNEL_TOKEN": "c" * 64, "KNOWLEDGE_SERVICE_TOKEN": TOKEN, "INFERENCE_TOKEN": TOKEN,
           "ASSISTANT_SERVICE_TOKEN": TOKEN, "KNOWLEDGE_DB_PASSWORD": "k" * 24, "ASSISTANT_DB_PASSWORD": "a" * 24,
           "TEAM_BACKUP_ROOT": "D:/meal_backups", "BACKUP_MIRROR_ROOT": "E:/meal_offsite",
           "CLOUD_INTERNAL_URL": "https://cloud-internal.team-domain.tw", "CLOUD_INTERNAL_TOKEN": TOKEN,
           "CLOUD_ACCESS_CLIENT_ID": "id-12345678", "CLOUD_ACCESS_CLIENT_SECRET": "s" * 40}
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class CloudPreflightTests(unittest.TestCase):
    def check(self, **overrides):
        from ops.preflight import check
        return check(cloud_env(**overrides), side="cloud")

    def test_complete_production_settings_pass(self):
        errors, warnings = self.check()
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_public_hostname_and_origin_must_match_https(self):
        errors, _ = self.check(ALLOWED_ORIGINS="http://meal.team-domain.tw")
        self.assertIn("ALLOWED_ORIGINS 必須只有 https://<PUBLIC_HOSTNAME>", errors)
        errors, _ = self.check(PUBLIC_HOSTNAME="localhost", ALLOWED_ORIGINS="https://localhost")
        self.assertTrue(any(e.startswith("PUBLIC_HOSTNAME") for e in errors))

    def test_trycloudflare_quick_tunnel_is_not_a_production_hostname(self):
        errors, _ = self.check(PUBLIC_HOSTNAME="abc-def.trycloudflare.com",
                               ALLOWED_ORIGINS="https://abc-def.trycloudflare.com")
        self.assertTrue(any("trycloudflare" in e for e in errors))

    def test_secrets_must_be_set_long_and_not_examples(self):
        errors, _ = self.check(POSTGRES_PASSWORD="short", CLOUDFLARE_TUNNEL_TOKEN=None,
                               INTERNAL_API_TOKEN="replace_with_random_token_please_1234567")
        self.assertIn("POSTGRES_PASSWORD 太短（至少 16 字元）", errors)
        self.assertIn("CLOUDFLARE_TUNNEL_TOKEN 未設定", errors)
        self.assertIn("INTERNAL_API_TOKEN 仍是範例值", errors)

    def test_cookie_must_stay_secure(self):
        errors, _ = self.check(COOKIE_SECURE="false")
        self.assertIn("COOKIE_SECURE 必須為 true", errors)

    def test_backups_need_an_independent_location_outside_data_root(self):
        errors, _ = self.check(BACKUP_MIRROR_ROOT=None)
        self.assertTrue(any(e.startswith("BACKUP_MIRROR_ROOT 未設定") for e in errors))
        errors, _ = self.check(PROJECT_BACKUP_ROOT="/srv/meal/data/backups")
        self.assertIn("PROJECT_BACKUP_ROOT 不可位於 Data Root 之內", errors)
        errors, _ = self.check(BACKUP_MIRROR_ROOT="/srv/meal/backups/offsite")
        self.assertIn("BACKUP_MIRROR_ROOT 不可位於 PROJECT_BACKUP_ROOT 之內", errors)
        errors, _ = self.check(PROJECT_DATA_ROOT="data")
        self.assertIn("PROJECT_DATA_ROOT 必須是絕對路徑", errors)

    def test_team_services_must_use_https_tunnel_and_access_token(self):
        errors, _ = self.check(KNOWLEDGE_SERVICE_URL="http://192.168.1.20:8091")
        self.assertIn("KNOWLEDGE_SERVICE_URL 必須是 https（經 Cloudflare Tunnel）", errors)
        errors, _ = self.check(TEAM_ACCESS_CLIENT_SECRET=None)
        self.assertIn("TEAM_ACCESS_CLIENT_SECRET 未設定", errors)

    def test_missing_team_services_only_warn_because_cloud_features_still_work(self):
        errors, warnings = self.check(RECOGNITION_SERVICE_URL=None, KNOWLEDGE_SERVICE_URL=None,
                                      ASSISTANT_OPENAI_SERVICE_URL=None, TEAM_ACCESS_CLIENT_ID=None,
                                      TEAM_ACCESS_CLIENT_SECRET=None)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 3)

    def test_cloud_model_assistant_needs_openai_key_and_internal_token(self):
        errors, _ = self.check(OPENAI_API_KEY=None)
        self.assertIn("OPENAI_API_KEY 未設定：飲食小幫手改用雲端模型生成，必須設定", errors)
        errors, _ = self.check(INTERNAL_API_TOKEN=None)
        self.assertIn("INTERNAL_API_TOKEN 未設定：團隊電腦呼叫雲端模型與內部 API 需要", errors)

    def test_errors_never_echo_secret_values(self):
        errors, warnings = self.check(POSTGRES_PASSWORD="tooshort-secret")
        self.assertNotIn("tooshort-secret", " ".join(errors + warnings))

    def test_database_url_must_point_to_the_internal_db_service(self):
        errors, _ = self.check(DATABASE_URL=None)
        self.assertIn("DATABASE_URL 未設定（填 install_real_nutrition 產生的連線）", errors)
        errors, _ = self.check(DATABASE_URL="postgresql+psycopg://app:x@203.0.113.5:5432/meal_prod")
        self.assertIn("DATABASE_URL 必須連到 Compose 內部的 db 服務", errors)


class TeamPreflightTests(unittest.TestCase):
    def check(self, **overrides):
        from ops.preflight import check
        return check(team_env(**overrides), side="team")

    def test_complete_team_settings_pass(self):
        self.assertEqual(self.check(), ([], []))

    def test_team_computer_must_not_hold_the_cloud_database_url(self):
        errors, _ = self.check(DATABASE_URL="postgresql://postgres:x@cloud/meal")
        self.assertTrue(any(e.startswith("DATABASE_URL 不應設定在團隊電腦") for e in errors))

    def test_cloud_internal_url_must_be_https(self):
        errors, _ = self.check(CLOUD_INTERNAL_URL="http://cloud-internal.team-domain.tw")
        self.assertIn("CLOUD_INTERNAL_URL 必須是 https（經 Cloudflare Tunnel 的內部 hostname）", errors)

    def test_cloud_model_assistant_needs_cloud_internal_url(self):
        errors, _ = self.check(CLOUD_INTERNAL_URL=None)
        self.assertIn("CLOUD_INTERNAL_URL 未設定：飲食小幫手經雲端模型生成回答需要", errors)


class DeployOrderTests(unittest.TestCase):
    def run_deploy(self, *, preflight=(), backup=lambda: Path("/b/cloud/x-predeploy"), fail_at=None):
        from ops.deploy import deploy
        calls, out, envs = [], [], []

        def run(cmd, env=None):
            calls.append(cmd)
            envs.append(env)
            step = next((s for s in ("build", "alembic", "up") if s in cmd), None)
            return 1 if step == fail_at else 0
        code = deploy(preflight=lambda: list(preflight), backup=backup, run=run, out=out.append,
                      migrate_env={"DATABASE_URL": "admin-url"})
        self.envs = envs
        return code, calls, out

    def test_backup_happens_before_build_migration_and_start(self):
        code, calls, out = self.run_deploy()
        self.assertEqual(code, 0)
        self.assertEqual([next(s for s in ("build", "alembic", "up") if s in c) for c in calls],
                         ["build", "alembic", "up"])
        self.assertIn("-f", calls[0])
        self.assertIn("compose.prod.yaml", calls[0])
        self.assertTrue(any("部署前備份" in line for line in out))
        self.assertTrue(any("不代表五人容量已驗證" in line for line in out))

    def test_only_the_migration_gets_the_admin_connection(self):
        _, calls, _ = self.run_deploy()
        self.assertEqual(self.envs, [None, {"DATABASE_URL": "admin-url"}, None])
        self.assertIn("DATABASE_URL", calls[1])
        self.assertFalse(any("admin-url" in part for c in calls for part in c))

    def test_failed_preflight_changes_nothing(self):
        code, calls, _ = self.run_deploy(preflight=["PUBLIC_HOSTNAME 需設為正式網域"])
        self.assertEqual((code, calls), (2, []))

    def test_failed_backup_stops_before_any_change(self):
        def broken():
            raise RuntimeError("pg_dump failed password=hunter2-secret")
        code, calls, out = self.run_deploy(backup=broken)
        self.assertEqual((code, calls), (3, []))
        self.assertNotIn("hunter2-secret", " ".join(out))

    def test_failed_migration_never_downgrades_and_points_to_restore(self):
        code, calls, out = self.run_deploy(fail_at="alembic")
        self.assertEqual(code, 5)
        self.assertFalse(any("downgrade" in " ".join(c) for c in calls))
        self.assertFalse(any("up" in c for c in calls))
        self.assertTrue(any("ops.restore" in line for line in out))


class EnvAndRedactionTests(unittest.TestCase):
    def test_redact_hides_url_credentials_bearer_and_key_values(self):
        from ops.env import redact
        text = redact("postgresql://postgres:pw@db/x Bearer abc.def token=xyz OPENAI_API_KEY: sk-123")
        for secret in ("pw@", "abc.def", "xyz", "sk-123"):
            self.assertNotIn(secret, text)

    def test_safe_error_keeps_type_and_redacts(self):
        from ops.env import safe_error
        self.assertEqual(safe_error(RuntimeError("password=abc")), "RuntimeError: password=***")

    def test_load_env_reads_file_but_process_environment_wins(self):
        from ops.env import load_env
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.prod"
            path.write_text("# 註解\nexport A=\"from-file\"\nB='b'\nT17_ENV_PROBE=file\n", encoding="utf-8")
            os.environ["T17_ENV_PROBE"] = "process"
            self.addCleanup(os.environ.pop, "T17_ENV_PROBE", None)
            env = load_env(path)
        self.assertEqual((env["A"], env["B"], env["T17_ENV_PROBE"]), ("from-file", "b", "process"))


class RetentionWithoutDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t17-retention-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def fake_backup(self, created):
        from ops.backup import FORMAT
        d = self.root / created.strftime("%Y%m%dT%H%M%SZ")
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"format": FORMAT, "created_at": created.isoformat()}),
                                         encoding="utf-8")
        return d

    def test_older_than_seven_days_pruned_newest_and_recent_kept(self):
        from ops.backup import prune_backups
        old = self.fake_backup(NOW - timedelta(days=8))
        edge = self.fake_backup(NOW - timedelta(days=6, hours=23))
        new = self.fake_backup(NOW)
        partial = self.root / "20261101T000000Z.partial"
        partial.mkdir()
        os.utime(partial, ((NOW - timedelta(days=2)).timestamp(),) * 2)
        removed = prune_backups(self.root, keep_days=7, now=NOW)
        self.assertEqual(sorted(removed), sorted([old, partial]))
        self.assertTrue(edge.exists() and new.exists())

    def test_only_backup_is_never_pruned(self):
        from ops.backup import prune_backups
        only = self.fake_backup(NOW - timedelta(days=30))
        self.assertEqual(prune_backups(self.root, keep_days=7, now=NOW), [])
        self.assertTrue(only.exists())


class DemoModeTests(unittest.TestCase):
    def test_extracts_the_latest_quick_tunnel_url_but_not_the_api_endpoint(self):
        from ops.standby import extract_tunnel_url
        log = ("INF Requesting new quick Tunnel on https://api.trycloudflare.com...\n"
               "INF |  https://old-name-one.trycloudflare.com  |\n"
               "INF |  https://Fresh-Name-Two.trycloudflare.com  |\n")
        self.assertEqual(extract_tunnel_url(log), "https://fresh-name-two.trycloudflare.com")
        self.assertIsNone(extract_tunnel_url("INF starting\n"))

    def test_generated_demo_env_has_random_secrets_and_no_dev_secrets_printed(self):
        from ops.standby import GENERATED_SECRETS, render_demo_env
        values = iter(f"{i:02d}" * 32 for i in range(20))
        text = render_demo_env({"OPENAI_API_KEY": "sk-dev"}, token=lambda: next(values))
        lines = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
        self.assertEqual(lines["PUBLIC_HOSTNAME"], "")
        self.assertEqual(len({lines[k] for k in GENERATED_SECRETS}), len(GENERATED_SECRETS))
        self.assertEqual(lines["OPENAI_API_KEY"], "sk-dev")

    def test_set_value_rewrites_one_key_only(self):
        from ops.standby import set_key
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.standby"
            path.write_text("A=1\nPUBLIC_HOSTNAME=\nB=2\n", encoding="utf-8")
            set_key(path, "PUBLIC_HOSTNAME", "x.trycloudflare.com")
            set_key(path, "NEW_KEY", "v")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "A=1\nPUBLIC_HOSTNAME=x.trycloudflare.com\nB=2\nNEW_KEY=v\n")


class ComposeFilesTests(unittest.TestCase):
    """部署檔案的靜態檢查：不開連入埠、Caddy 不處理憑證、排程程序在正式環境啟動。"""

    ROOT = Path(__file__).resolve().parents[1]

    def text(self, name):
        return (self.ROOT / name).read_text(encoding="utf-8")

    def service(self, text, name):
        """compose 檔中某個服務的區塊（到下一個同層服務或頂層鍵為止）。"""
        import re
        match = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z]|\Z)", text)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_production_publishes_no_ports_and_runs_tunnel_and_scheduler(self):
        text = self.text("compose.prod.yaml")
        for name in ("db", "backend", "frontend"):
            self.assertIn("    ports: !reset []", self.service(text, name), name)
        self.assertIn("TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN:?", text)
        self.assertIn('command: ["tunnel", "--no-autoupdate", "run"]', text)
        self.assertIn("    profiles: !reset []", self.service(text, "scheduler"))
        self.assertIn('AUTO_MIGRATE: "false"', text)
        self.assertNotIn("--url", text)

    def test_demo_uses_quick_tunnel_without_ports(self):
        text = self.text("compose.standby.yaml")
        self.assertIn('"--url", "http://frontend:80"', text)
        for name in ("db", "backend", "frontend"):
            self.assertIn("    ports: !reset []", self.service(text, name), name)

    def test_standby_never_reuses_the_demo_branch_names(self):
        # t11-t20-demo 的展示站在同一台電腦用 overcooked-demo 專案、.env.demo 與 YOLO 8775；共用就會互相覆蓋
        import re
        text = self.text("compose.standby.yaml")
        self.assertRegex(text, r"(?m)^name: overcooked-standby$")
        for container in re.findall(r"container_name: (\S+)", text):
            self.assertTrue(container.startswith("overcooked-standby-"), container)
        for name in ("compose.standby.yaml", "scripts/standby-up.ps1", "scripts/standby-down.ps1", "ops/standby.py"):
            body = self.text(name)
            for taken in ("overcooked-demo", ".env.demo", "compose.demo.yaml", "8775"):
                self.assertNotIn(taken, body, f"{name}: {taken}")

    def test_caddy_only_serves_plain_http_inside_the_network(self):
        for name in ("frontend/Caddyfile", "admin-dashboard/Caddyfile"):
            caddy = self.text(name)
            self.assertIn(":80 {", caddy, name)
            self.assertNotRegex(caddy, r"(?m)^\s*tls\s", name)
            self.assertIn("auto_https off", caddy, name)

    def test_team_compose_binds_only_loopback(self):
        import re
        ports = re.findall(r'^\s+- "([^"]+:\d+)"\s*$', self.text("deploy/team/compose.yaml"), re.M)
        self.assertTrue(ports)
        for port in ports:
            self.assertTrue(port.startswith("127.0.0.1:"), port)


if __name__ == "__main__":
    unittest.main()
