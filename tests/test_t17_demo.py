"""單機 demo 模式（compose.demo.yaml、ops.demo、demo 腳本）：快速通道網址擷取、.env.demo 產生與補鍵、合併後的 compose 設定。

不啟動任何容器、不執行任何腳本；compose 測試只執行 docker compose config，腳本只做文字檢查。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.demo import (FROM_DEV, GENERATED_SECRETS, GENERATION_KEYS, ensure_keys, extract_tunnel_url, init_env,
                      read_env_file, set_key)

ROOT = Path(__file__).resolve().parents[1]
NEW_SECRETS = ("SIM_MARKET_HANDOFF_KEY", "ASSISTANT_SERVICE_TOKEN", "ASSISTANT_DB_PASSWORD")

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

    def test_new_secrets_are_generated_and_new_keys_default_to_empty(self):
        self.assertTrue(set(NEW_SECRETS) <= set(GENERATED_SECRETS))
        dev = self.dir / ".env.dev"
        dev.write_text("ASSISTANT_DATA_DIR=D:/data/assistant_rag\n", encoding="utf-8")
        init_env(self.dir / ".env.demo", dev, out=lambda _: None)
        env = read_env_file(self.dir / ".env.demo")
        for key in NEW_SECRETS:
            self.assertGreaterEqual(len(env[key].encode()), 32, key)
        # 空值 = 功能不可用（超市 503、T04 證據 422），不可放佔位主機或假版本
        self.assertEqual(env["SIM_MARKET_URL"], "")
        self.assertEqual([env[k] for k in GENERATION_KEYS], ["", "", ""])
        self.assertEqual((env["ASSISTANT_DATA_DIR"], env["ASSISTANT_RERANKER_MODEL_DIR"]), ("D:/data/assistant_rag", ""))

    def test_ensure_keys_appends_only_missing_keys_and_is_idempotent(self):
        demo = self.dir / ".env.demo"
        old = ("# 舊版 demo 設定\r\nPUBLIC_HOSTNAME=old.trycloudflare.com\r\nPOSTGRES_PASSWORD=keep-pw\r\n"
               "INTERNAL_API_TOKEN=keep-t\r\nOPENAI_API_KEY=sk-old\r\nDEMO_YOLO_PORT=9999").encode("utf-8")
        demo.write_bytes(old)
        dev = self.dir / ".env.dev"
        dev.write_text("ASSISTANT_RERANKER_MODEL_DIR=D:/models/bge-reranker-v2-m3\nOPENAI_API_KEY=sk-dev\n", encoding="utf-8")
        out = []
        added = ensure_keys(demo, dev, out=out.append)
        raw = demo.read_bytes()
        self.assertTrue(raw.startswith(old), "既有內容必須逐位元組保留")
        env = read_env_file(demo)
        self.assertEqual((env["POSTGRES_PASSWORD"], env["INTERNAL_API_TOKEN"], env["OPENAI_API_KEY"],
                          env["PUBLIC_HOSTNAME"], env["DEMO_YOLO_PORT"]),
                         ("keep-pw", "keep-t", "sk-old", "old.trycloudflare.com", "9999"))
        for key in NEW_SECRETS + ("KNOWLEDGE_DB_PASSWORD", "INFERENCE_TOKEN"):
            self.assertIn(key, added)
            self.assertGreaterEqual(len(env[key]), 32, key)
        self.assertNotIn("POSTGRES_PASSWORD", added)
        self.assertEqual(env["SIM_MARKET_URL"], "")
        self.assertEqual([env[k] for k in GENERATION_KEYS], ["", "", ""])
        self.assertEqual(env["ASSISTANT_RERANKER_MODEL_DIR"], "D:/models/bge-reranker-v2-m3")
        self.assertEqual(raw.count(b"OPENAI_API_KEY="), 1)
        printed = "\n".join(out)
        for key in NEW_SECRETS:
            self.assertIn(key, printed)
            self.assertNotIn(env[key], printed)
        self.assertEqual(ensure_keys(demo, dev, out=lambda _: None), [])
        self.assertEqual(demo.read_bytes(), raw)


# 新版之前產生的 .env.demo 只有這些鍵；compose.demo.yaml 新增的鍵不可是 :? 必填
OLD_DEMO_ENV = {"POSTGRES_PASSWORD": "placeholder-pw", "KNOWLEDGE_DB_PASSWORD": "placeholder-kpw",
                "KNOWLEDGE_SERVICE_TOKEN": "placeholder-k", "INFERENCE_TOKEN": "placeholder-i",
                "INTERNAL_API_TOKEN": "placeholder-t", "PUBLIC_HOSTNAME": "abc-def.trycloudflare.com"}
DEMO_ENV = {**OLD_DEMO_ENV, "SIM_MARKET_HANDOFF_KEY": "placeholder-market-key-0123456789abcdef",
            "ASSISTANT_SERVICE_TOKEN": "placeholder-a", "ASSISTANT_DB_PASSWORD": "placeholder-apw"}
ALL_PROFILES = ("tunnel", "tools", "sim-market", "assistant", "assistant-tools")
COMPOSE_FILES = ("compose.yaml", "compose.demo.yaml")


def demo_compose_config(env: dict, profiles=ALL_PROFILES):
    """與 demo 腳本相同：-p overcooked-demo --env-file <檔>；不讀 repo 的 .env，也不讓目前程序的同名環境變數混入。"""
    referenced = set()
    for name in COMPOSE_FILES:
        referenced |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", (ROOT / name).read_text(encoding="utf-8")))
    proc_env = {k: v for k, v in os.environ.items() if k not in referenced and not k.startswith("COMPOSE_")}
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / ".env.demo"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        cmd = ["docker", "compose", "-p", "overcooked-demo", "--env-file", str(env_file)]
        for name in COMPOSE_FILES:
            cmd += ["-f", str(ROOT / name)]
        for profile in profiles:
            cmd += ["--profile", profile]
        proc = subprocess.run([*cmd, "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", env=proc_env, timeout=120)
    return proc.returncode, (json.loads(proc.stdout) if proc.returncode == 0 else proc.stderr)


@unittest.skipUnless(shutil.which("docker"), "needs docker compose")
class DemoComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code, cfg = demo_compose_config(DEMO_ENV)
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

    def test_old_env_file_without_new_keys_still_renders(self):
        for profiles in (ALL_PROFILES, ("tunnel", "tools"), ()):
            code, cfg = demo_compose_config(OLD_DEMO_ENV, profiles)
            self.assertEqual(code, 0, cfg)
        env = cfg["services"]["backend"]["environment"]
        self.assertEqual((env["SIM_MARKET_HANDOFF_KEY"], env["ASSISTANT_SERVICE_TOKEN"]), ("", ""))

    def test_frontend_is_the_3d_scene_app(self):
        fe = self.cfg["services"]["frontend"]
        self.assertTrue(fe["build"]["context"].replace("\\", "/").endswith("/frontend-3d"), fe["build"])
        self.assertEqual(fe["container_name"], "overcooked-demo-frontend")

    def test_market_has_its_own_tunnel_and_is_not_an_allowed_origin(self):
        services = self.cfg["services"]
        market = services["sim-supermarket"]
        self.assertEqual((market["container_name"], market["restart"]), ("overcooked-demo-sim-supermarket", "unless-stopped"))
        cf = services["cloudflared-market"]
        self.assertEqual(cf["image"], "cloudflare/cloudflared:2026.9.0")
        self.assertEqual(cf["command"], ["tunnel", "--no-autoupdate", "--url", "http://sim-supermarket:80"])
        self.assertEqual((cf["container_name"], cf["restart"], cf["profiles"]),
                         ("overcooked-demo-cloudflared-market", "no", ["tunnel"]))
        env = services["backend"]["environment"]
        self.assertEqual(env["SIM_MARKET_URL"], "")
        self.assertEqual(env["ALLOWED_ORIGINS"], "https://abc-def.trycloudflare.com")
        code, cfg = demo_compose_config({**DEMO_ENV, "SIM_MARKET_URL": "https://shop-xyz.trycloudflare.com"})
        self.assertEqual(code, 0, cfg)
        env = cfg["services"]["backend"]["environment"]
        self.assertEqual(env["SIM_MARKET_URL"], "https://shop-xyz.trycloudflare.com")
        self.assertNotIn("shop-xyz", env["ALLOWED_ORIGINS"])

    def test_backend_feature_defaults(self):
        env = self.cfg["services"]["backend"]["environment"]
        self.assertEqual([env[k] for k in GENERATION_KEYS], ["", "", ""])
        self.assertEqual(env["ASSISTANT_SERVICE_URL"], "http://assistant-rag:8093")
        self.assertEqual(env["ASSISTANT_OPENAI_SERVICE_URL"], "http://assistant-rag-openai:8093")
        self.assertEqual(env["SIM_MARKET_HANDOFF_KEY"], DEMO_ENV["SIM_MARKET_HANDOFF_KEY"])
        self.assertEqual(env["ASSISTANT_SERVICE_TOKEN"], "placeholder-a")
        self.assertEqual(env["KNOWLEDGE_EXPLAIN_PROVIDER"], "openai")

    def test_assistant_services(self):
        services = self.cfg["services"]
        for name in ("assistant-db", "assistant-reranker", "assistant-rag", "assistant-rag-openai"):
            self.assertEqual(services[name]["profiles"], ["assistant"], name)
        self.assertEqual(services["rag-tools"]["profiles"], ["assistant-tools"])
        rag, openai = services["assistant-rag"], services["assistant-rag-openai"]
        self.assertEqual(rag["build"]["dockerfile"], "deploy/team/assistant.Dockerfile")
        self.assertEqual(rag["environment"]["RAG_LLM_PROVIDER"], "ollama")
        self.assertEqual(rag["environment"]["RAG_OLLAMA_URL"], "http://host.docker.internal:11434")
        self.assertEqual(openai["image"], rag["image"])
        self.assertEqual(openai["environment"]["RAG_LLM_PROVIDER"], "openai")
        self.assertEqual(openai["environment"]["CLOUD_INTERNAL_URL"], "http://backend:8000")
        self.assertEqual(openai["environment"]["CLOUD_INTERNAL_TOKEN"], "placeholder-t")
        self.assertNotIn("OPENAI_API_KEY", openai["environment"])
        self.assertEqual([v["source"] for v in services["assistant-db"]["volumes"] if v["type"] == "volume"],
                         ["demo_assistant_pg"])
        self.assertIn("demo_assistant_pg", self.cfg["volumes"])
        # 未設定 ASSISTANT_DATA_DIR 時不可掛到 repo（空字串的 bind 來源會解析成專案目錄）
        for name in ("assistant-rag", "assistant-rag-openai", "rag-tools"):
            (data,) = [v for v in services[name]["volumes"] if v["target"] == "/assistant-data"]
            self.assertEqual(data["type"], "bind")
            self.assertFalse(data["bind"]["create_host_path"], name)
            self.assertNotEqual(Path(data["source"]).resolve(), ROOT.resolve(), name)


class DemoScriptTests(unittest.TestCase):
    """demo 腳本只做文字檢查（不執行）：備份先於升級、不 downgrade、資料包每次載入、Purge 提示小幫手索引。"""

    UP = (ROOT / "scripts/demo-up.ps1").read_text(encoding="utf-8-sig")
    DOWN = (ROOT / "scripts/demo-down.ps1").read_text(encoding="utf-8-sig")

    def _line(self, text, pattern):
        lines = text.splitlines()
        hits = [i for i, line in enumerate(lines) if re.search(pattern, line)]
        self.assertEqual(len(hits), 1, (pattern, [lines[i] for i in hits]))
        return hits[0], lines

    def test_backup_precedes_the_upgrade_and_downgrade_is_never_invoked(self):
        backup, _ = self._line(self.UP, r"-m ops\.backup cloud --container overcooked-demo-db --database meal_planner")
        verify, _ = self._line(self.UP, r"verify_backup")
        upgrade, _ = self._line(self.UP, r"^\s*Dc run .*alembic .*upgrade head")
        self.assertLess(backup, verify)
        self.assertLess(verify, upgrade)
        for line in re.sub(r"<#.*?#>", "", self.UP, flags=re.S).splitlines():
            code = re.sub(r"'[^']*'|\"[^\"]*\"", "", line.split("#", 1)[0])
            self.assertNotIn("downgrade", code, line)

    def test_release_bundle_loads_every_run_without_a_count_guard(self):
        i, lines = self._line(self.UP, r"scripts/load_release_bundle\.py --bundle release_data/nutrition-v1")
        self.assertTrue(lines[i].startswith("Dc run --rm tools "), lines[i])
        self.assertNotIn("nutrition_data_releases", self.UP)
        ensure, _ = self._line(self.UP, r"-m ops\.demo ensure-keys")
        build, _ = self._line(self.UP, r"^Dc .*\bbuild\b")
        self.assertLess(ensure, build)

    def test_optional_image_builds_only_degrade(self):
        core, lines = self._line(self.UP, r"^Dc build backend knowledge-rag frontend$")
        self.assertIn("Fail", lines[core + 1])
        for pattern, flag in ((r"Dc --profile sim-market build sim-supermarket$", "$SkipMarket = $true"),
                              (r"Dc --profile assistant build assistant-rag$", "$UseAssistant = $false")):
            i, lines = self._line(self.UP, pattern)
            self.assertNotIn("Fail ", lines[i + 1])
            self.assertIn(flag, lines[i + 1])
        self.assertIn("if ($UseAssistant) {\n    Dc --profile assistant build assistant-rag", self.UP)

    def test_relative_backup_root_is_resolved_before_the_bind_mount(self):
        resolve, _ = self._line(self.UP, r"GetUnresolvedProviderPathFromPSPath\(\$BackupRoot\)")
        stage, _ = self._line(self.UP, r"\$Stage = Join-Path \$BackupRoot")
        self.assertLess(resolve, stage)

    def test_assistant_data_script_builds_its_image_before_running_tools(self):
        text = (ROOT / "scripts/assistant-data-demo.ps1").read_text(encoding="utf-8-sig")
        self.assertLess(text.index("Dc build assistant-rag"), text.index("'rag-tools'"))

    def test_start_dev_records_only_containers_it_started(self):
        text = (ROOT / "scripts/start-dev.ps1").read_text(encoding="utf-8-sig")
        skip = text.index("--filter status=running --format '{{.Names}}') { continue }")
        self.assertLess(skip, text.index("docker start $c"))

    def test_purge_prompt_names_the_assistant_index(self):
        self.assertIn("demo_assistant_pg", self.DOWN)
        prompt = next(line for line in self.DOWN.splitlines() if "Read-Host" in line)
        self.assertIn("飲食小幫手索引", prompt)
        for profile in ("tunnel", "tools", "sim-market", "assistant"):
            self.assertIn(f"'--profile', '{profile}'", self.DOWN)


if __name__ == "__main__":
    unittest.main()


class BackendImageConfigTests(unittest.TestCase):
    """後端映像要帶齊程式讀取的設定檔：曾逐檔 COPY 漏掉 nutrition_as_is_categories 等，demo 生成全部失敗。"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_dockerfile_copies_the_whole_config_directory(self):
        dockerfile = (self.ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY config /app/config", dockerfile)

    def test_every_config_file_the_code_reads_exists(self):
        import re
        names = set()
        for path in list((self.ROOT / "backend/app").rglob("*.py")) + list((self.ROOT / "nutrition_model").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            names |= set(re.findall(r'config/([a-z_]+\.json)', text))
            names |= set(re.findall(r'"config" / "([a-z_]+\.json)"', text))
        self.assertTrue(names)
        missing = sorted(n for n in names if not (self.ROOT / "config" / n).is_file())
        self.assertEqual(missing, [])
