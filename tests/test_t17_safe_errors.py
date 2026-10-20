"""S8／S9：維運與知識管理指令印出的錯誤不可帶連線字串或密碼；設定檔讀取用公開函式。

不連資料庫、不呼叫任何外部服務：以注入或 patch 讓指令在第一步就失敗。
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SECRET = "hunter2-very-secret"
LEAKY = RuntimeError(f'connection to "postgresql://postgres:{SECRET}@db:5432/meal_planner" failed: '
                     f"password={SECRET} token={SECRET} Authorization: Bearer {SECRET}")


class SafeErrorTests(unittest.TestCase):
    def test_safe_error_masks_connection_strings_and_credentials(self):
        from ops.env import safe_error
        text = safe_error(LEAKY)
        self.assertNotIn(SECRET, text)
        self.assertIn("RuntimeError", text)
        self.assertIn("db:5432/meal_planner", text)  # 主機與資料庫名稱仍可協助排查

    def test_backup_failure_message_has_no_secrets(self):
        from ops import backup
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.object(backup, "create_backup", side_effect=LEAKY), \
                redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = backup.main(["cloud", "--backup-root", tmp])
        self.assertEqual(code, 1)
        self.assertIn("backup FAILED", err.getvalue())
        self.assertNotIn(SECRET, err.getvalue())

    def test_deploy_backup_failure_message_has_no_secrets(self):
        from ops.deploy import deploy
        lines = []

        def backup():
            raise LEAKY
        code = deploy(preflight=lambda: [], backup=backup, run=lambda cmd: 0, out=lines.append)
        self.assertEqual(code, 3)
        self.assertNotIn(SECRET, "\n".join(lines))

    def test_knowledge_admin_error_has_no_secrets(self):
        from knowledge_rag.admin import Deps, main

        def store():
            raise LEAKY
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = main(["list"], deps=Deps(store=store))
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, err.getvalue())

    def test_env_file_loader_is_public_and_does_not_override_environment(self):
        from ops.env import load_env
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# 註解\nexport A="1"\nB=2\n', encoding="utf-8")
            with patch.dict("os.environ", {"B": "env"}):
                env = load_env(path)
        self.assertEqual((env["A"], env["B"]), ("1", "env"))


if __name__ == "__main__":
    unittest.main()
