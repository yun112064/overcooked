"""T17 維運指令的純邏輯（不需資料庫）：設定檢查、部署前備份順序、秘密遮蔽、保留期、快速通道網址、compose 檔。"""
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def prod_env(**overrides):
    from ops.prodenv import GENERATED_SECRETS, PATH_DEFAULTS
    env = {key: f"{i:02d}" * 32 for i, key in enumerate(GENERATED_SECRETS)}
    env.update(dict(PATH_DEFAULTS))
    env.update({"PUBLIC_HOSTNAME": "fresh-name.trycloudflare.com", "BACKUP_MIRROR_ROOT": "E:/meal-prod-offsite",
                "DATABASE_URL": "postgresql+psycopg://app:secret@db:5432/meal_planner_prod",
                "OPENAI_API_KEY": "sk-" + "k" * 40})
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class PreflightTests(unittest.TestCase):
    def check(self, **overrides):
        from ops.preflight import check
        return check(prod_env(**overrides))

    def test_generated_settings_pass(self):
        self.assertEqual(self.check(), ([], []))

    def test_fresh_env_from_prod_up_passes_before_the_database_is_installed(self):
        from ops.preflight import check
        from ops.prodenv import read_env_file, render_prod_env
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.prod"
            path.write_text(render_prod_env({}), encoding="utf-8")
            errors, warnings = check(read_env_file(path))
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith("BACKUP_MIRROR_ROOT 未設定") for w in warnings))

    def test_example_file_is_rejected_until_generated(self):
        from ops.env import load_env
        from ops.preflight import check
        errors, _ = check(load_env(ROOT / ".env.prod.example"))
        self.assertTrue(any(e.startswith("POSTGRES_PASSWORD") for e in errors))

    def test_quick_tunnel_hostname_is_the_normal_case(self):
        self.assertEqual(self.check(PUBLIC_HOSTNAME="")[0], [])
        _, warnings = self.check(PUBLIC_HOSTNAME="meal.example.tw")
        self.assertTrue(any("trycloudflare" in w for w in warnings))

    def test_secrets_must_be_set_and_long(self):
        errors, _ = self.check(POSTGRES_PASSWORD="short", ASSISTANT_SERVICE_TOKEN=None)
        self.assertIn("POSTGRES_PASSWORD 太短（至少 32 字元）", errors)
        self.assertIn("ASSISTANT_SERVICE_TOKEN 未設定", errors)

    def test_backups_need_an_independent_location_outside_data_root(self):
        _, warnings = self.check(BACKUP_MIRROR_ROOT=None)
        self.assertTrue(any(w.startswith("BACKUP_MIRROR_ROOT 未設定") for w in warnings))
        errors, _ = self.check(PROJECT_BACKUP_ROOT="D:/meal-prod/data/backups")
        self.assertIn("PROJECT_BACKUP_ROOT 不可位於 Data Root 之內", errors)
        errors, _ = self.check(BACKUP_MIRROR_ROOT="d:\\Meal-Prod\\backups\\offsite")
        self.assertIn("BACKUP_MIRROR_ROOT 不可位於 PROJECT_BACKUP_ROOT 之內", errors)
        errors, _ = self.check(ASSISTANT_DATA_DIR="assistant")
        self.assertIn("ASSISTANT_DATA_DIR 必須是絕對路徑", errors)

    def test_missing_openai_key_only_warns_because_the_assistant_is_local(self):
        errors, warnings = self.check(OPENAI_API_KEY=None)
        self.assertEqual(errors, [])
        self.assertTrue(any("飲食小幫手用本機模型" in w for w in warnings))

    def test_errors_never_echo_secret_values(self):
        errors, warnings = self.check(POSTGRES_PASSWORD="tooshort-secret")
        self.assertNotIn("tooshort-secret", " ".join(errors + warnings))

    def test_database_url_must_point_to_the_internal_db_service(self):
        errors, _ = self.check(DATABASE_URL="postgresql+psycopg://app:x@127.0.0.1:55435/meal_prod")
        self.assertIn("DATABASE_URL 必須連到 Compose 內部的 db 服務", errors)


class DeployOrderTests(unittest.TestCase):
    def run_deploy(self, *, preflight=(), backup=lambda: Path("/b/cloud/x-predeploy"), fail=False):
        from ops.deploy import deploy
        calls, out, envs = [], [], []

        def run(cmd, env=None):
            calls.append(cmd)
            envs.append(env)
            return 1 if fail else 0
        code = deploy(preflight=lambda: list(preflight), backup=backup, run=run, out=out.append,
                      migrate_env={"DATABASE_URL": "admin-url"})
        self.envs = envs
        return code, calls, out

    def test_backup_happens_before_the_migration(self):
        code, calls, out = self.run_deploy()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("alembic", calls[0])
        self.assertIn("compose.prod.yaml", calls[0])
        self.assertTrue(any("部署前備份" in line for line in out))
        self.assertTrue(any("不代表五人容量已驗證" in line for line in out))

    def test_only_the_migration_gets_the_admin_connection_and_never_on_the_command_line(self):
        _, calls, _ = self.run_deploy()
        self.assertEqual(self.envs, [{"DATABASE_URL": "admin-url"}])
        self.assertFalse(any("admin-url" in part for c in calls for part in c))

    def test_failed_preflight_changes_nothing(self):
        code, calls, _ = self.run_deploy(preflight=["POSTGRES_PASSWORD 未設定"])
        self.assertEqual((code, calls), (2, []))

    def test_failed_backup_stops_before_any_change(self):
        def broken():
            raise RuntimeError("pg_dump failed password=hunter2-secret")
        code, calls, out = self.run_deploy(backup=broken)
        self.assertEqual((code, calls), (3, []))
        self.assertNotIn("hunter2-secret", " ".join(out))

    def test_failed_migration_never_downgrades_and_points_to_restore(self):
        code, calls, out = self.run_deploy(fail=True)
        self.assertEqual(code, 5)
        self.assertFalse(any("downgrade" in " ".join(c) for c in calls))
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

    def test_reranker_weights_are_not_copied_into_daily_backups(self):
        from ops.backup import DATA_ROOT_TOP_EXCLUDE
        self.assertIn("models", DATA_ROOT_TOP_EXCLUDE)


class ProdEnvTests(unittest.TestCase):
    def test_extracts_the_latest_quick_tunnel_url_but_not_the_api_endpoint(self):
        from ops.prodenv import extract_tunnel_url
        log = ("INF Requesting new quick Tunnel on https://api.trycloudflare.com...\n"
               "INF |  https://old-name-one.trycloudflare.com  |\n"
               "INF |  https://Fresh-Name-Two.trycloudflare.com  |\n")
        self.assertEqual(extract_tunnel_url(log), "https://fresh-name-two.trycloudflare.com")
        self.assertIsNone(extract_tunnel_url("INF starting\n"))

    def test_generated_env_has_unique_random_secrets_and_only_copies_allowed_dev_keys(self):
        from ops.prodenv import GENERATED_SECRETS, render_prod_env
        values = iter(f"{i:02d}" * 32 for i in range(20))
        text = render_prod_env({"OPENAI_API_KEY": "sk-dev", "DATABASE_URL": "dev-db"}, token=lambda: next(values))
        lines = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
        self.assertEqual(lines["PUBLIC_HOSTNAME"], "")
        self.assertEqual(len({lines[k] for k in GENERATED_SECRETS}), len(GENERATED_SECRETS))
        self.assertEqual(lines["OPENAI_API_KEY"], "sk-dev")
        self.assertNotIn("DATABASE_URL", lines)
        self.assertEqual(lines["PROD_YOLO_PORT"], "8776")

    def test_set_value_rewrites_one_key_only(self):
        from ops.prodenv import set_key
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.prod"
            path.write_text("A=1\nPUBLIC_HOSTNAME=\nB=2\n", encoding="utf-8")
            set_key(path, "PUBLIC_HOSTNAME", "x.trycloudflare.com")
            set_key(path, "NEW_KEY", "v")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "A=1\nPUBLIC_HOSTNAME=x.trycloudflare.com\nB=2\nNEW_KEY=v\n")


class ComposeFilesTests(unittest.TestCase):
    """部署檔案的靜態檢查：不開連入埠、Caddy 不處理憑證、不與開發環境或 demo 展示站共用名稱。"""

    def text(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def service(self, text, name):
        """compose 檔中某個服務的區塊（到下一個同層服務或頂層鍵為止）。"""
        match = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z]|\Z)", text)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_nothing_is_published_on_the_host(self):
        text = self.text("compose.prod.yaml")
        for name in ("db", "backend", "frontend"):
            self.assertIn("    ports: !reset []", self.service(text, name), name)
        for name in ("knowledge-db", "knowledge-rag", "assistant-db", "assistant-reranker", "assistant-rag",
                     "cloudflared"):
            self.assertNotIn("ports:", self.service(text, name), name)

    def test_production_builds_the_new_frontend(self):
        frontend = self.service(self.text("compose.prod.yaml"), "frontend")
        self.assertIn("context: ./overcooked-frontend/frontend", frontend)
        self.assertIn("dockerfile: Dockerfile", frontend)
        self.assertIn("NPM_CONFIG_REGISTRY: ${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org}", frontend)
        dockerfile = self.text("overcooked-frontend/frontend/Dockerfile")
        self.assertIn("ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org\nRUN npm ci\n", dockerfile)
        self.assertIn("RUN npm run build\n", dockerfile)

    def test_quick_tunnel_like_the_demo_branch(self):
        text = self.text("compose.prod.yaml")
        tunnel = self.service(text, "cloudflared")
        self.assertIn('"--url", "http://frontend:80"', tunnel)
        self.assertIn('profiles: ["tunnel"]', tunnel)
        self.assertIn('restart: "no"', tunnel)
        self.assertNotIn("TUNNEL_TOKEN", text)
        self.assertIn("    profiles: !reset []", self.service(text, "scheduler"))
        self.assertIn('AUTO_MIGRATE: "false"', text)

    def test_never_reuses_the_v3_development_database_or_the_demo_site_names(self):
        text = self.text("compose.prod.yaml")
        self.assertRegex(text, r"(?m)^name: meal-prod$")
        for container in re.findall(r"container_name: (\S+)", text):
            self.assertTrue(container.startswith("meal-prod-"), container)
        for volume in re.findall(r"(?m)^    name: (\S+)$", text):
            self.assertTrue(volume.startswith("meal-prod-"), volume)
        body = text.split("\nname:")[1]
        for name in ("compose.prod.yaml", "scripts/prod-up.ps1", "scripts/prod-down.ps1", "ops/prodenv.py"):
            content = body if name == "compose.prod.yaml" else self.text(name)
            for taken in ("overcooked-1102-v3", "overcooked-demo", ".env.demo", "8775"):
                self.assertNotIn(taken, content, f"{name}: {taken}")
        from ops.backup import DEFAULTS
        self.assertTrue(all(container.startswith("meal-prod-") for container, _ in DEFAULTS.values()))

    def test_assistant_runs_on_local_models_and_reaches_the_reranker_inside_the_network(self):
        text = self.text("compose.prod.yaml")
        self.assertIn("RAG_LLM_PROVIDER: ollama", text)
        self.assertIn("RAG_RERANK_URL: http://assistant-reranker:80", text)
        self.assertIn("RAG_OLLAMA_URL: http://host.docker.internal:11434", text)
        backend = self.service(text, "backend")
        self.assertIn("ASSISTANT_SERVICE_URL: http://assistant-rag:8093", backend)
        self.assertIn('ASSISTANT_OPENAI_SERVICE_URL: ""', backend)
        self.assertIn("KNOWLEDGE_SERVICE_URL: http://knowledge-rag:8091", backend)
        # 原文語料只讀掛進服務；只有一次性工具可以寫
        self.assertIn(":/assistant-data:ro", self.service(text, "assistant-rag"))
        self.assertIn(":/model:ro", self.service(text, "assistant-reranker"))
        for name in ("backend", "worker", "knowledge-rag", "assistant-rag", "rag-tools"):
            self.assertIn('"host.docker.internal:host-gateway"', self.service(text, name), name)

    def test_caddy_only_serves_plain_http_inside_the_network(self):
        for name in ("frontend/Caddyfile", "overcooked-frontend/frontend/Caddyfile", "admin-dashboard/Caddyfile"):
            caddy = self.text(name)
            self.assertIn(":80 {", caddy, name)
            self.assertNotRegex(caddy, r"(?m)^\s*tls\s", name)
            self.assertIn("auto_https off", caddy, name)


if __name__ == "__main__":
    unittest.main()
