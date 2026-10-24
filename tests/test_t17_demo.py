"""單機 demo 模式（compose.demo.yaml、ops.demo）：快速通道網址擷取、.env.demo 產生、合併後的 compose 設定。

不啟動任何容器；compose 測試只執行 docker compose config。
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from ops.demo import FROM_DEV, GENERATED_SECRETS, extract_tunnel_url, init_env, read_env_file, set_key
from test_t17_deploy import compose_config

CLOUDFLARED_LOG = """2026-10-24T08:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-10-24T08:00:01Z INF +--------------------------------------------------------------------------------------------+
2026-10-24T08:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-10-24T08:00:01Z INF |  https://Gentle-River-Abc123-Demo.trycloudflare.com                                        |
2026-10-24T08:00:01Z INF +--------------------------------------------------------------------------------------------+
2026-10-24T08:00:02Z INF Registered tunnel connection connIndex=0 location=tpe01
"""


class TunnelUrlTests(unittest.TestCase):
    def test_extracts_quick_tunnel_url(self):
        self.assertEqual(extract_tunnel_url(CLOUDFLARED_LOG), "https://gentle-river-abc123-demo.trycloudflare.com")

    def test_ignores_registration_endpoint_and_takes_latest(self):
        log = ("ERR failed to request quick Tunnel: Post \"https://api.trycloudflare.com/tunnel\"\n"
               "INF https://old-one.trycloudflare.com\nINF https://new-one.trycloudflare.com\n")
        self.assertEqual(extract_tunnel_url(log), "https://new-one.trycloudflare.com")
        self.assertIsNone(extract_tunnel_url("ERR https://api.trycloudflare.com/tunnel 429"))
        self.assertIsNone(extract_tunnel_url(""))

    def test_rejects_lookalike_hosts(self):
        self.assertIsNone(extract_tunnel_url("https://evil.trycloudflare.com.example.net"))


class DemoEnvTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_init_env_generates_secrets_and_copies_dev_values_without_printing_them(self):
        dev = self.dir / ".env.dev"
        dev.write_text("OPENAI_API_KEY=\nOPENAI_API_KEY=sk-dev-value\nKNOWLEDGE_AUTHORIZATION_REF=docs/auth.pdf\n"
                       "DEV_DB_PASSWORD=dev-only\n", encoding="utf-8")
        out = []
        self.assertTrue(init_env(self.dir / ".env.demo", dev, out=out.append))
        env = read_env_file(self.dir / ".env.demo")
        for key in GENERATED_SECRETS:
            self.assertGreaterEqual(len(env[key]), 32, key)
        self.assertEqual(len({env[k] for k in GENERATED_SECRETS}), len(GENERATED_SECRETS))
        self.assertEqual((env["OPENAI_API_KEY"], env["KNOWLEDGE_AUTHORIZATION_REF"]), ("sk-dev-value", "docs/auth.pdf"))
        self.assertNotIn("DEV_DB_PASSWORD", env)
        self.assertEqual(env["PUBLIC_HOSTNAME"], "")
        printed = "\n".join(out)
        for value in [env[k] for k in GENERATED_SECRETS] + ["sk-dev-value"]:
            self.assertNotIn(value, printed)

    def test_init_env_keeps_existing_file(self):
        demo = self.dir / ".env.demo"
        demo.write_text("POSTGRES_PASSWORD=keep\n", encoding="utf-8")
        self.assertFalse(init_env(demo, None, out=lambda _: None))
        self.assertEqual(demo.read_text(encoding="utf-8"), "POSTGRES_PASSWORD=keep\n")

    def test_init_env_without_dev_file_leaves_paid_keys_empty(self):
        out = []
        init_env(self.dir / ".env.demo", self.dir / "missing", out=out.append)
        env = read_env_file(self.dir / ".env.demo")
        self.assertTrue(all(env[k] == "" for k in FROM_DEV))
        self.assertTrue(any("OPENAI_API_KEY" in line for line in out))

    def test_set_key_replaces_or_appends(self):
        demo = self.dir / ".env.demo"
        demo.write_text("# c\nPUBLIC_HOSTNAME=\nPOSTGRES_PASSWORD=x\n", encoding="utf-8")
        set_key(demo, "PUBLIC_HOSTNAME", "a-b.trycloudflare.com")
        set_key(demo, "NEW_KEY", "v")
        self.assertEqual(demo.read_text(encoding="utf-8"),
                         "# c\nPUBLIC_HOSTNAME=a-b.trycloudflare.com\nPOSTGRES_PASSWORD=x\nNEW_KEY=v\n")
        with self.assertRaises(ValueError):
            set_key(demo, "BAD KEY", "v")
        with self.assertRaises(ValueError):
            set_key(demo, "OK", "two\nlines")


DEMO_ENV = {"POSTGRES_PASSWORD": "placeholder-pw", "KNOWLEDGE_DB_PASSWORD": "placeholder-kpw",
            "KNOWLEDGE_SERVICE_TOKEN": "placeholder-k", "INFERENCE_TOKEN": "placeholder-i",
            "INTERNAL_API_TOKEN": "placeholder-t", "PUBLIC_HOSTNAME": "abc-def.trycloudflare.com"}


@unittest.skipUnless(shutil.which("docker"), "needs docker compose")
class DemoComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {**DEMO_ENV, "COMPOSE_PROFILES": "tunnel,tools"}
        code, cfg = compose_config(["compose.yaml", "compose.demo.yaml"], env)
        assert code == 0, cfg
        cls.cfg = cfg

    def test_nothing_is_published_on_the_host(self):
        self.assertEqual(self.cfg["name"], "overcooked-demo")
        for name, svc in self.cfg["services"].items():
            self.assertFalse(svc.get("ports"), f"{name} 不可對主機開埠")

    def test_quick_tunnel_points_only_at_frontend_and_needs_no_token(self):
        cf = self.cfg["services"]["cloudflared"]
        self.assertEqual(cf["image"], "cloudflare/cloudflared:2026.9.0")
        self.assertEqual(cf["command"], ["tunnel", "--no-autoupdate", "--url", "http://frontend:80"])
        self.assertNotIn("TUNNEL_TOKEN", cf.get("environment") or {})
        self.assertEqual(cf["restart"], "no")

    def test_backend_settings(self):
        env = self.cfg["services"]["backend"]["environment"]
        self.assertEqual((env["ALLOWED_ORIGINS"], env["COOKIE_SECURE"], env["AUTO_MIGRATE"]),
                         ("https://abc-def.trycloudflare.com", "true", "false"))
        self.assertEqual(env["RECOGNITION_SERVICE_URL"], "http://host.docker.internal:8775")
        self.assertEqual(env["KNOWLEDGE_SERVICE_URL"], "http://knowledge-rag:8766")
        self.assertEqual(self.cfg["services"]["tools"]["environment"]["CLOUD_INTERNAL_URL"], "http://backend:8000")

    def test_data_lives_in_demo_volumes_only(self):
        services = self.cfg["services"]
        self.assertEqual([v["source"] for v in services["db"]["volumes"]], ["demo_pg"])
        self.assertEqual(sorted(v["source"] for v in services["backend"]["volumes"]),
                         ["demo_backups", "demo_data", "demo_runtime"])
        names = {svc.get("container_name") for svc in services.values()} - {None}
        self.assertNotIn("overcooked-dev-db", names)
        self.assertTrue(all(n.startswith("overcooked-demo-") for n in names), names)


if __name__ == "__main__":
    unittest.main()
