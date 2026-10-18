import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.api import health


class TestHealth(unittest.TestCase):

    def test_health_check(self):
        self.assertEqual(
            health.health_check(),
            {"status": "ok", "service": "backend"},
        )

    def test_database_health_check_ok(self):
        engine = MagicMock()
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.scalar_one.return_value = 1

        with patch.object(health, "engine", engine):
            result = health.database_health_check()

        self.assertEqual(result, {"status": "ok", "service": "database"})

    def test_database_unavailable_returns_503_without_details(self):
        engine = MagicMock()
        engine.connect.side_effect = OperationalError(
            "SELECT 1", {}, Exception("password=FAKE_DB_SECRET")
        )

        with (
            patch.object(health, "engine", engine),
            patch.object(health, "api_logger") as logger,
        ):
            with self.assertRaises(HTTPException) as ctx:
                health.database_health_check()

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("FAKE_DB_SECRET", str(ctx.exception.detail))
        self.assertEqual(
            logger.error.call_args.kwargs["extra"]["event"],
            "db_health_check_failed",
        )


if __name__ == "__main__":
    unittest.main()
