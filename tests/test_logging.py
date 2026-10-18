
import asyncio
import json
import logging
import os
import time
import unittest

from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import main
from app.core import log_cleanup
from app.core.logging_config import (
    JsonFormatter,
    PreservingRotatingFileHandler,
)


class TestLogging(unittest.TestCase):

    def test_daily_rotation(self):
        """測試跨 UTC 日期後輪替，並保留原有日誌。"""

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "api.jsonl"

            handler = PreservingRotatingFileHandler(
                path,
                maxBytes=10 * 1024 * 1024,
                backupCount=0,
                encoding="utf-8",
            )

            logger = logging.Logger(
                "daily_rotation_test",
                level=logging.INFO,
            )
            logger.addHandler(handler)

            try:
                first = logging.LogRecord(
                    logger.name,
                    logging.INFO,
                    __file__,
                    0,
                    "day-one",
                    (),
                    None,
                )
                logger.handle(first)

                second = logging.LogRecord(
                    logger.name,
                    logging.INFO,
                    __file__,
                    0,
                    "day-two",
                    (),
                    None,
                )

                # 模擬兩天後產生新日誌
                second.created = first.created + 2 * 86400
                logger.handle(second)

            finally:
                logger.removeHandler(handler)
                handler.close()

            archives = [
                p for p in Path(tmp).glob("api.jsonl.*")
                if p.is_file()
            ]

            self.assertEqual(len(archives), 1)
            self.assertIn(
                "day-one",
                archives[0].read_text(encoding="utf-8"),
            )
            self.assertIn(
                "day-two",
                path.read_text(encoding="utf-8"),
            )



    def test_sensitive_redaction(self):
        """敏感資料不得經由任意訊息進入正式 JSONL。"""

        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            (
                '{"password":"DEMO_PASS",'
                '"apiKey":"FAKE_API_KEY_123",'
                '"sessionId":"FAKE_SESSION_456",'
                '"allergies":"FAKE_PRIVATE_HEALTH",'
                '"photo":"FAKE_IMAGE_BYTES",'
                '"prompt":"FAKE_MODEL_CONTENT"}'
            ),
            (),
            None,
        )

        record.event = "health_check"
        record.source = "api_key=DEMO_SOURCE"

        result = JsonFormatter().format(record)
        data = json.loads(result)

        self.assertEqual(data["event"], "health_check")
        self.assertEqual(data["source"], "api")
        self.assertEqual(
            data["message"],
            "Health check succeeded",
        )

        for secret in (
            "DEMO_PASS",
            "FAKE_API_KEY_123",
            "FAKE_SESSION_456",
            "FAKE_PRIVATE_HEALTH",
            "FAKE_IMAGE_BYTES",
            "FAKE_MODEL_CONTENT",
            "DEMO_SOURCE",
        ):
            self.assertNotIn(secret, result)

        # 未核准事件不得寫入任意內容
        record.event = "user_FAKE_PRIVATE_HEALTH"
        unknown = json.loads(JsonFormatter().format(record))

        self.assertEqual(
            unknown["event"],
            "unknown_event",
        )
        self.assertEqual(
            unknown["message"],
            "Unapproved log message omitted",
        )
        self.assertNotIn(
            "FAKE_PRIVATE_HEALTH",
            json.dumps(unknown),
        )


    def test_rotation(self):
        """測試輪替後所有日誌仍存在。"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"

            handler = PreservingRotatingFileHandler(
                path,
                maxBytes=300,
                backupCount=0,
                encoding="utf-8",
            )

            logger = logging.Logger(
                "rotation_test",
                level=logging.INFO,
            )
            logger.addHandler(handler)

            try:
                for i in range(10):
                    logger.info(f"test-{i}: " + "x" * 100)
            finally:
                logger.removeHandler(handler)
                handler.close()

            files = list(Path(tmp).glob("test.jsonl*"))
            total = sum(
                len(p.read_text().splitlines())
                for p in files
            )

            self.assertGreater(len(files), 1)
            self.assertEqual(total, 10)

    def test_cleanup(self):
        """測試清理預覽、刪除及保護檔案。"""
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)

            old = folder / "api.jsonl.1"
            recent = folder / "api.jsonl.20261013T034013318148Z"
            held = folder / "process.jsonl.20261013T034013318189Z"
            active = folder / "api.jsonl"

            for path, size in [
                (old, 128),
                (recent, 384),
                (held, 384),
                (active, 384),
            ]:
                path.write_bytes(b"x" * size)

            (folder / (held.name + ".hold")).touch()

            old_time = time.time() - 15 * 86400
            os.utime(old, (old_time, old_time))

            with (
                patch.object(log_cleanup, "LOG_DIR", folder),
                patch.object(log_cleanup, "MAX_TOTAL_BYTES", 1024),
                patch.object(log_cleanup, "opened_files", return_value=set()),
                redirect_stdout(StringIO()),
            ):
                log_cleanup.cleanup(apply=False)

                self.assertTrue(old.exists())
                self.assertTrue(recent.exists())

                log_cleanup.cleanup(apply=True)

            self.assertFalse(old.exists())
            self.assertFalse(recent.exists())
            self.assertTrue(held.exists())
            self.assertTrue(active.exists())

    def test_schedule(self):
        """測試排程重複執行，而且保持預覽模式。"""
        calls = []

        def fake_cleanup(*, apply=False):
            calls.append(apply)

        async def run_schedule():
            task = asyncio.create_task(
                main.scheduled_log_cleanup()
            )

            try:
                await asyncio.sleep(0.35)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        with (
            patch.object(main, "cleanup", fake_cleanup),
            patch.object(main, "CLEANUP_INTERVAL", 0.1),
            patch.dict(os.environ, {"LOG_CLEANUP_APPLY": "false"}),
        ):
            asyncio.run(run_schedule())

        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(value is False for value in calls))


    def test_opened_file_protection(self):
        """正在使用中的輪替檔不可刪除。"""
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "api.jsonl.1"

            path.write_text("test log\n")

            old_time = time.time() - 15 * 86400
            os.utime(path, (old_time, old_time))

            with (
                patch.object(log_cleanup, "LOG_DIR", folder),
                redirect_stdout(StringIO()),
            ):
                # 情境一：模擬檔案正在使用
                with path.open("rb") as active_file:
                    info = os.fstat(active_file.fileno())
                    opened = {(info.st_dev, info.st_ino)}

                    with patch.object(
                        log_cleanup,
                        "opened_files",
                        return_value=opened,
                    ):
                        log_cleanup.cleanup(apply=True)
                        self.assertTrue(path.exists())

                # 情境二：模擬檔案已經關閉
                with patch.object(
                    log_cleanup,
                    "opened_files",
                    return_value=set(),
                ):
                    log_cleanup.cleanup(apply=True)

                self.assertFalse(path.exists())

    def test_same_timestamp_rotation_keeps_every_archive(self):
        """同一時間戳記多次輪替時，不可覆蓋或遺失封存檔。"""
        from datetime import datetime, timezone
        from app.core import logging_config

        fixed = datetime(2026, 10, 18, 7, 0, 0, 0, tzinfo=timezone.utc)

        class FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"

            handler = PreservingRotatingFileHandler(
                path,
                maxBytes=300,
                backupCount=0,
                encoding="utf-8",
            )

            logger = logging.Logger(
                "same_timestamp_rotation_test",
                level=logging.INFO,
            )
            logger.addHandler(handler)

            try:
                with patch.object(logging_config, "datetime", FrozenNow):
                    for i in range(6):
                        logger.info(f"test-{i}: " + "x" * 200)
            finally:
                logger.removeHandler(handler)
                handler.close()

            archives = sorted(
                p.name for p in Path(tmp).glob("test.jsonl.*")
            )
            lines = [
                line
                for p in Path(tmp).glob("test.jsonl*")
                for line in p.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(len(lines), 6)
            self.assertEqual(len(archives), 5)
            self.assertEqual(len(set(archives)), 5)

            # 加序號的封存檔仍屬可清理的輪替檔
            for name in archives:
                self.assertTrue(
                    log_cleanup.ROTATED_PATTERN.fullmatch(name),
                    name,
                )
            self.assertFalse(
                log_cleanup.ROTATED_PATTERN.fullmatch(
                    archives[-1] + ".hold"
                )
            )

    def test_budget_exceeded_is_written_to_scheduler_log(self):
        """清理後仍超過空間預算時，需在正式 Log 留下提示。"""
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            active = folder / "api.jsonl"
            active.write_bytes(b"x" * 2048)

            with (
                patch.object(log_cleanup, "LOG_DIR", folder),
                patch.object(log_cleanup, "MAX_TOTAL_BYTES", 1024),
                patch.object(log_cleanup, "opened_files", return_value=set()),
                patch.object(log_cleanup, "scheduler_logger") as logger,
                redirect_stdout(StringIO()),
            ):
                log_cleanup.cleanup(apply=True)

            self.assertTrue(active.exists())
            logger.warning.assert_called_once()
            extra = logger.warning.call_args.kwargs["extra"]
            self.assertEqual(extra["event"], "log_cleanup_budget_exceeded")

            record = logging.LogRecord(
                "scheduler", logging.WARNING, "", 0, "x", (), None
            )
            record.event = extra["event"]
            data = json.loads(JsonFormatter().format(record))
            self.assertEqual(data["source"], "scheduler")
            self.assertNotEqual(data["event"], "unknown_event")


if __name__ == "__main__":
    unittest.main(verbosity=2)
