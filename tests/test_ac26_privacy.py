import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.logging_config import JsonFormatter, PreservingRotatingFileHandler

ROOT = Path(__file__).resolve().parents[1]
SECRET = "T06_FAKE_PRIVATE_HEALTH_123"


class TestAC26Privacy(unittest.TestCase):

    def test_jsonl_privacy(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "api.jsonl"

            handler = PreservingRotatingFileHandler(
                path, maxBytes=10 * 1024 * 1024,
                backupCount=0, encoding="utf-8"
            )
            handler.setFormatter(JsonFormatter())

            logger = logging.Logger("ac26_test", level=logging.INFO)
            logger.addHandler(handler)

            try:
                for event in ("health_check", "unknown_" + SECRET):
                    record = logging.LogRecord(
                        "ac26_test", logging.INFO, "", 0,
                        "家庭健康資料：" + SECRET, (), None
                    )
                    record.event = event
                    record.source = SECRET
                    logger.handle(record)
            finally:
                logger.removeHandler(handler)
                handler.close()

            content = path.read_text(encoding="utf-8")

            self.assertNotIn(SECRET, content)

            for line in content.splitlines():
                data = json.loads(line)
                self.assertEqual(
                    set(data),
                    {"time", "level", "event", "source", "message"}
                )

    def test_uvicorn_access_log_disabled(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("--no-access-log", dockerfile)


if __name__ == "__main__":
    unittest.main()
