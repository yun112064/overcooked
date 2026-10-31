"""決策 #23：scripts/dev-env.ps1（start-dev.ps1 與 dev-run.ps1 共用）的設定邏輯，隔離測試。

只在暫存目錄的假 .env.dev 上執行，不連資料庫、不啟動任何服務；沒有 Windows PowerShell 時略過。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "dev-env.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

BASE_ENV = """DEV_DB_PORT=55432
DEV_DB_NAME=meal_planner_dev2
DEV_DB_PASSWORD=pw-not-real
KNOWLEDGE_SERVICE_URL=
KNOWLEDGE_SERVICE_TOKEN=
INFERENCE_TOKEN=keep-me
"""

KEYS = ("DATABASE_URL", "KNOWLEDGE_DATABASE_URL", "KNOWLEDGE_SERVICE_URL", "KNOWLEDGE_SERVICE_TOKEN", "KNOWLEDGE_TOKEN",
        "KNOWLEDGE_PORT", "CLOUD_INTERNAL_URL", "CLOUD_INTERNAL_TOKEN", "INTERNAL_API_TOKEN", "APP_ENV",
        "INFERENCE_TOKEN")


@unittest.skipUnless(POWERSHELL, "needs Windows PowerShell")
class DevEnvScriptTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.env_file = self.dir / ".env.dev"

    def ps(self, body: str) -> subprocess.CompletedProcess:
        script = f". '{LIB}'\n$ErrorActionPreference = 'Stop'\n{body}"
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def write(self, text: str) -> None:
        self.env_file.write_text(text, encoding="utf-8")

    def ensure_secrets(self) -> subprocess.CompletedProcess:
        return self.ps(f"Add-MissingDevSecrets '{self.env_file}' @('KNOWLEDGE_SERVICE_TOKEN', 'INTERNAL_API_TOKEN')")

    def read_cfg(self) -> dict:
        lines = self.env_file.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))

    def test_generates_missing_and_empty_tokens_without_printing_them(self):
        self.write(BASE_ENV)
        res = self.ensure_secrets()
        self.assertEqual(res.returncode, 0, res.stderr)
        cfg = self.read_cfg()
        for key in ("KNOWLEDGE_SERVICE_TOKEN", "INTERNAL_API_TOKEN"):
            self.assertGreaterEqual(len(cfg[key]), 32, key)
            self.assertNotIn(cfg[key], res.stdout + res.stderr)
        self.assertNotEqual(cfg["KNOWLEDGE_SERVICE_TOKEN"], cfg["INTERNAL_API_TOKEN"])
        self.assertEqual(cfg["INFERENCE_TOKEN"], "keep-me")
        self.assertEqual(list(cfg).count("KNOWLEDGE_SERVICE_TOKEN"), 1)

    def test_existing_tokens_are_kept_on_rerun(self):
        self.write(BASE_ENV)
        self.ensure_secrets()
        first = self.read_cfg()
        self.assertEqual(self.ensure_secrets().returncode, 0)
        self.assertEqual(self.read_cfg(), first)

    def environment(self, extra: str = "") -> dict:
        self.write(BASE_ENV + "INTERNAL_API_TOKEN=internal-not-real\n" + extra)
        names = ",".join(f"'{k}'" for k in KEYS)
        res = self.ps(f"$cfg = Read-DevConfig '{self.env_file}'\n"
                      f"Set-DevEnvironment $cfg '{self.dir}' | Out-Null\n"
                      f"$o = @{{}}; foreach ($k in @({names})) {{ $o[$k] = [Environment]::GetEnvironmentVariable($k) }}\n"
                      "$o | ConvertTo-Json -Compress")
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_knowledge_and_internal_api_wiring_follow_dev_db_name(self):
        env = self.environment("KNOWLEDGE_SERVICE_TOKEN=kn-not-real\n")
        self.assertTrue(env["DATABASE_URL"].endswith("@127.0.0.1:55432/meal_planner_dev2"))
        self.assertEqual(env["KNOWLEDGE_DATABASE_URL"],
                         "postgresql://postgres:pw-not-real@127.0.0.1:55432/meal_planner_dev2_knowledge")
        self.assertEqual(env["KNOWLEDGE_SERVICE_URL"], "http://127.0.0.1:8766")
        self.assertEqual((env["KNOWLEDGE_SERVICE_TOKEN"], env["KNOWLEDGE_TOKEN"]), ("kn-not-real", "kn-not-real"))
        self.assertEqual((env["CLOUD_INTERNAL_URL"], env["CLOUD_INTERNAL_TOKEN"]),
                         ("http://127.0.0.1:8000", "internal-not-real"))
        self.assertEqual(env["APP_ENV"], "development")

    def test_skip_knowledge_leaves_service_unconfigured(self):
        env = self.environment("KNOWLEDGE_SERVICE_TOKEN=kn-not-real\nDEV_SKIP_KNOWLEDGE=1\n")
        self.assertFalse(env["KNOWLEDGE_SERVICE_URL"])

    def test_explicit_knowledge_url_is_respected(self):
        # BASE_ENV 已有空白 KNOWLEDGE_SERVICE_URL=；後面非空的設定優先
        env = self.environment("KNOWLEDGE_SERVICE_TOKEN=kn-not-real\nKNOWLEDGE_SERVICE_URL=https://knowledge.example.invalid\n")
        self.assertEqual(env["KNOWLEDGE_SERVICE_URL"], "https://knowledge.example.invalid")


if __name__ == "__main__":
    unittest.main()
