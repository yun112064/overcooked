"""T10：持久背景工作與付費額度控制（AC18、AC20、AC25）。"""
import json
import logging
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text

from db_support import make_client, migrate_fresh, requires_db, session_factory, truncate_all

PASSWORD = "correct-horse-42"
MODEL = "gpt-5.4-mini-2026-03-17"


class FormatterFieldTests(unittest.TestCase):
    def test_only_well_formed_tracing_fields_are_logged(self):
        from app.core.logging_config import JsonFormatter

        record = logging.LogRecord("p", logging.INFO, "", 0, "x", (), None)
        record.event = "job_failed"
        record.job_id = "0b5e6c0e-1f2a-4c3d-9e8f-0123456789ab"
        record.error_code = "NEEDS_MANUAL_RETRY"
        record.duration_ms = 12
        data = json.loads(JsonFormatter().format(record))
        self.assertEqual((data["job_id"], data["error_code"], data["duration_ms"]),
                         (record.job_id, "NEEDS_MANUAL_RETRY", 12))
        record.job_id = "secret value"
        record.error_code = "password=hunter2"
        data = json.loads(JsonFormatter().format(record))
        self.assertNotIn("job_id", data)
        self.assertNotIn("error_code", data)


def failing(ctx):
    """不呼叫付費服務、直接失敗的處理函式（只測工作流程，不測生成內容）。"""
    from app.services.jobs import JobFailure
    raise JobFailure("GENERATION_FAILED", "測試用失敗")


def draft(member_id):
    return {"meal_date": "2026-08-20", "meal_type": "dinner", "budget": "300", "staple": "白飯",
            "dish_count": 2, "participants": [{"member_id": member_id}]}


@requires_db
class T10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        self.factory = session_factory()
        self.client = make_client()
        self.client.post("/auth/register", json={"email": "owner@example.test", "password": PASSWORD})
        self.client.post("/household", json={"name": "測試家庭"})
        member = self.client.post("/members", json={
            "display_name": "成員A", "birth_date": "1996-03-01", "equation_sex": "M", "weight_kg": "70",
            "activity_category": "sedentary", "goal": "maintain", "portion": "1",
            "allergen_ingredient_ids": [], "avoided_ingredient_ids": []}).json()
        self.request = self.client.post("/meal-requests", json=draft(member["id"])).json()
        self.member_id = member["id"]

    # --- helpers ---

    def submit(self, key="key-00000001", version=None, client=None):
        client = client or self.client
        return client.post(f"/meal-requests/{self.request['id']}/submit",
                           json={"version": version or self.request["version"]},
                           headers={"Idempotency-Key": key})

    def job_row(self, job_id):
        from app.db.models import BackgroundJob
        with self.factory() as db:
            return db.get(BackgroundJob, job_id)

    def request_status(self):
        return self.client.get(f"/meal-requests/{self.request['id']}").json()["status"]

    def expire_lease(self, job_id):
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE background_jobs SET lease_expires_at = now() - interval '1 second' "
                            "WHERE id = :id"), {"id": job_id})

    def make_available(self, job_id):
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE background_jobs SET available_at = now() WHERE id = :id"), {"id": job_id})

    def ledger(self):
        from app.db.models import BudgetMonth
        from app.services import spend
        with self.factory() as db:
            return db.get(BudgetMonth, spend.month_key())

    # --- 冪等送出 ---

    def test_same_key_same_content_returns_same_job(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202, first.text)
        second = self.submit()
        self.assertEqual(second.json()["job"]["id"], first.json()["job"]["id"])
        self.assertEqual((first.json()["created"], second.json()["created"]), (True, False))
        jobs = self.client.get("/jobs").json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.request_status(), "queued")

    def test_reused_key_with_different_content_is_rejected(self):
        from app.jobs.worker import run_once
        self.assertEqual(self.submit().status_code, 202)
        run_once(self.factory, "worker-a", {"meal_generation": failing})  # failed，問卷可再修改
        updated = self.client.put(f"/meal-requests/{self.request['id']}",
                                  json={"version": 1, "draft": {**draft(self.member_id), "budget": "350"}}).json()
        reused = self.submit(version=updated["version"])
        self.assertEqual(reused.status_code, 409)
        self.assertEqual(reused.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        fresh = self.submit(key="key-00000002", version=updated["version"])
        self.assertEqual(fresh.status_code, 202)

    def test_concurrent_submits_with_same_key_create_one_job(self):
        clients = [make_client() for _ in range(4)]
        for c in clients:
            c.cookies.update(self.client.cookies)
        results = []
        threads = [threading.Thread(target=lambda c=c: results.append(self.submit(client=c))) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for r in results if r.status_code == 202]
        self.assertTrue(ok)
        self.assertEqual(len({r.json()["job"]["id"] for r in ok}), 1)
        self.assertEqual(len(self.client.get("/jobs").json()["jobs"]), 1)

    def test_submit_requires_key_complete_current_version_and_idle_request(self):
        res = self.client.post(f"/meal-requests/{self.request['id']}/submit", json={"version": 1})
        self.assertEqual(res.status_code, 422)
        stale = self.submit(version=99)
        self.assertEqual(stale.json()["error"]["code"], "VERSION_CONFLICT")
        partial = self.client.post("/meal-requests", json={"meal_type": "lunch"}).json()
        res = self.client.post(f"/meal-requests/{partial['id']}/submit", json={"version": 1},
                               headers={"Idempotency-Key": "key-partial-1"})
        self.assertEqual(res.json()["error"]["details"]["reason"], "INCOMPLETE")
        self.assertEqual(self.submit().status_code, 202)
        busy = self.submit(key="key-00000009")
        self.assertEqual(busy.json()["error"]["details"]["reason"], "ALREADY_RUNNING")
        edit = self.client.put(f"/meal-requests/{self.request['id']}",
                               json={"version": 1, "draft": draft(self.member_id)})
        self.assertEqual(edit.json()["error"]["details"]["reason"], "GENERATION_IN_PROGRESS")

    def test_jobs_are_household_scoped(self):
        job_id = self.submit().json()["job"]["id"]
        other = make_client()
        other.post("/auth/register", json={"email": "other@example.test", "password": PASSWORD})
        other.post("/household", json={"name": "別人家"})
        self.assertEqual(other.get(f"/jobs/{job_id}").status_code, 404)
        self.assertEqual(other.get("/jobs").json()["jobs"], [])
        res = other.post(f"/meal-requests/{self.request['id']}/submit", json={"version": 1},
                         headers={"Idempotency-Key": "key-00000001"})
        self.assertEqual(res.status_code, 404)

    def test_daily_rate_limit(self):
        from app.core.config import settings
        from app.jobs.worker import run_once
        self.assertEqual(self.submit().status_code, 202)
        run_once(self.factory, "worker-a", {"meal_generation": failing})
        with patch.object(settings, "generation_daily_limit", 1):
            res = self.submit(key="key-00000002")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["error"]["code"], "RATE_LIMITED")

    # --- 工作程序 ---

    def test_default_handler_without_model_service_fails_clearly_and_keeps_draft(self):
        # T14 取代原「尚未開放」占位：沒有可行資料庫菜單且未設定模型金鑰時明確失敗，不假裝產生菜單
        from app.core.config import settings
        from app.jobs.worker import run_once
        job_id = self.submit().json()["job"]["id"]
        with patch.object(settings, "openai_api_key", None), patch.object(settings, "knowledge_service_url", None):
            run_once(self.factory, "worker-a")
        job = self.client.get(f"/jobs/{job_id}").json()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "GENERATION_FAILED")
        self.assertIn("未設定", job["error"]["message"])
        self.assertEqual(job["attempts"], 1)
        self.assertEqual(self.request_status(), "failed")
        reread = self.client.get(f"/meal-requests/{self.request['id']}").json()
        self.assertEqual(reread["draft"]["budget"], "300")
        edited = self.client.put(f"/meal-requests/{self.request['id']}",
                                 json={"version": 1, "draft": draft(self.member_id)})
        self.assertEqual(edited.json()["status"], "draft")
        # 未呼叫任何付費服務
        self.assertEqual(self.ledger().reserved_twd + self.ledger().spent_twd, 0)

    def test_success_handler_reads_locked_version(self):
        from app.jobs.worker import run_once
        seen = {}

        def handler(ctx):
            ctx.stage("generating")
            seen.update(ctx.input_payload())
            return {"menu": "synthetic"}

        job_id = self.submit().json()["job"]["id"]
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        job = self.client.get(f"/jobs/{job_id}").json()
        self.assertEqual((job["status"], job["result_version"]), ("succeeded", 1))
        self.assertEqual(self.request_status(), "result_ready")
        self.assertEqual(seen["budget"], "300")
        self.assertEqual(self.job_row(job_id).result, {"menu": "synthetic"})

    def test_retry_is_limited_to_two_attempts(self):
        from app.jobs.worker import run_once
        from app.services.jobs import RetryableJobError
        calls = []

        def flaky(ctx):
            calls.append(ctx.job.attempts)
            raise RetryableJobError("temporary")

        job_id = self.submit().json()["job"]["id"]
        run_once(self.factory, "worker-a", {"meal_generation": flaky})
        row = self.job_row(job_id)
        self.assertEqual((row.status, row.attempts), ("queued", 1))
        self.assertGreater(row.available_at, datetime.now(timezone.utc))
        self.assertIsNone(run_once(self.factory, "worker-a", {"meal_generation": flaky}))  # 退避中
        self.make_available(job_id)
        run_once(self.factory, "worker-a", {"meal_generation": flaky})
        self.make_available(job_id)
        self.assertIsNone(run_once(self.factory, "worker-a", {"meal_generation": flaky}))
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.job_row(job_id).status, "failed")

    def test_concurrent_workers_claim_a_job_once(self):
        from app.services import jobs
        self.submit()
        claimed = []
        barrier = threading.Barrier(5)

        def worker(i):
            barrier.wait()
            job = jobs.claim(self.factory, f"worker-{i}")
            if job is not None:
                claimed.append(job.id)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(claimed), 1)

    def test_expired_lease_is_recovered_then_fails_after_limit(self):
        from app.services import jobs
        job_id = self.submit().json()["job"]["id"]
        first = jobs.claim(self.factory, "worker-crashed")
        self.expire_lease(job_id)
        self.assertEqual(jobs.recover_expired(self.factory), 1)
        self.assertEqual(self.job_row(job_id).status, "queued")
        # 被回收後，原工作程序的結果不得寫入
        self.assertFalse(jobs.complete(self.factory, first.id, "worker-crashed", {"late": True}))
        jobs.claim(self.factory, "worker-crashed-again")
        self.expire_lease(job_id)
        jobs.recover_expired(self.factory)
        row = self.job_row(job_id)
        self.assertEqual((row.status, row.error_code, row.attempts), ("failed", "GENERATION_FAILED", 2))
        self.assertIsNone(row.result)
        self.assertEqual(self.request_status(), "failed")

    def test_recovery_with_in_flight_paid_call_requires_manual_retry(self):
        from app.services import jobs, spend
        job_id = self.submit().json()["job"]["id"]
        job = jobs.claim(self.factory, "worker-crashed")
        spend.reserve(self.factory, call_key=f"{job_id}:1:menu", purpose="menu", provider="openai",
                      model=MODEL, max_input_tokens=3000, max_output_tokens=1000, job_id=job.id)
        reserved = self.ledger().reserved_twd
        self.expire_lease(job_id)
        jobs.recover_expired(self.factory)
        row = self.job_row(job_id)
        self.assertEqual((row.status, row.error_code), ("failed", "NEEDS_MANUAL_RETRY"))
        from app.db.models import UsageReservation
        with self.factory() as db:
            usage = db.scalar(select(UsageReservation))
        self.assertEqual(usage.status, "uncertain")
        # 不確定是否計費：預留仍占用額度，直到人工核對
        self.assertEqual(self.ledger().reserved_twd, reserved)
        spend.reconcile(self.factory, usage.id, actual_twd=Decimal("0.1"), note="provider usage checked")
        ledger = self.ledger()
        self.assertEqual((ledger.reserved_twd, ledger.spent_twd), (0, Decimal("0.1")))

    def test_uncertain_paid_call_in_handler_is_not_retried(self):
        from app.jobs.worker import run_once
        attempts = []

        def handler(ctx):
            attempts.append(ctx.job.attempts)

            def call():
                raise TimeoutError("read timeout after request was sent")
            return ctx.paid_call(name="menu", purpose="menu", provider="openai", model=MODEL,
                                 max_input_tokens=3000, max_output_tokens=1000, call=call)

        job_id = self.submit().json()["job"]["id"]
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        self.make_available(job_id)
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        row = self.job_row(job_id)
        self.assertEqual((row.status, row.error_code), ("failed", "NEEDS_MANUAL_RETRY"))
        self.assertEqual(attempts, [1])

    def test_not_billed_failure_releases_and_retries_with_new_call_key(self):
        from app.jobs.worker import run_once
        from app.services import spend
        outcomes = iter([spend.CallNotBilled("connection refused"), None])

        def handler(ctx):
            def call():
                exc = next(outcomes)
                if exc:
                    raise exc
                return {"ok": True}, {"input_tokens": 1200, "output_tokens": 300}
            return ctx.paid_call(name="menu", purpose="menu", provider="openai", model=MODEL,
                                 max_input_tokens=3000, max_output_tokens=1000, call=call)

        job_id = self.submit().json()["job"]["id"]
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        self.make_available(job_id)
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        self.assertEqual(self.job_row(job_id).status, "succeeded")
        from app.db.models import UsageReservation
        with self.factory() as db:
            rows = db.scalars(select(UsageReservation).order_by(UsageReservation.created_at)).all()
        self.assertEqual([r.status for r in rows], ["released", "settled"])
        self.assertEqual(len({r.call_key for r in rows}), 2)
        ledger = self.ledger()
        self.assertEqual(ledger.reserved_twd, 0)
        self.assertEqual(ledger.spent_twd, spend.cost_twd(MODEL, 1200, 300))

    # --- 額度 ---

    def test_settlement_uses_actual_usage(self):
        from app.services import spend
        result = spend.paid_call(self.factory, call_key="manual-1", purpose="test", provider="openai", model=MODEL,
                                 max_input_tokens=3000, max_output_tokens=1000,
                                 call=lambda: ("done", {"input_tokens": 1000, "output_tokens": 200}))
        self.assertEqual(result, "done")
        expected = spend.cost_twd(MODEL, 1000, 200)
        self.assertEqual(expected, Decimal("0.0545"))  # (0.75×1000 + 4.5×200)/1e6 USD × 33，無條件進位
        ledger = self.ledger()
        self.assertEqual((ledger.reserved_twd, ledger.spent_twd, ledger.committed_twd),
                         (0, expected, Decimal("1400")))
        with self.assertRaises(spend.UncertainBilling):
            spend.reserve(self.factory, call_key="manual-1", purpose="test", provider="openai", model=MODEL,
                          max_input_tokens=1, max_output_tokens=1)

    def test_unknown_model_is_refused_not_priced_zero(self):
        from app.services import spend
        with self.assertRaises(ValueError):
            spend.reserve(self.factory, call_key="m", purpose="test", provider="x", model="unpriced-model",
                          max_input_tokens=1, max_output_tokens=1)

    def test_concurrent_reservations_cannot_exceed_monthly_limit(self):
        from app.services import spend
        per_call = spend.cost_twd(MODEL, 3000, 1000)
        with self.factory() as db, db.begin():
            spend.ensure_month(db, spend.month_key())
            # 剩 3.5 次呼叫的額度
            db.execute(text("UPDATE budget_months SET committed_twd = limit_twd - :room"),
                       {"room": per_call * Decimal("3.5")})
        outcomes = []
        barrier = threading.Barrier(8)

        def reserve(i):
            barrier.wait()
            try:
                spend.reserve(self.factory, call_key=f"race-{i}", purpose="test", provider="openai", model=MODEL,
                              max_input_tokens=3000, max_output_tokens=1000)
                outcomes.append("ok")
            except spend.BudgetExhausted:
                outcomes.append("exhausted")

        threads = [threading.Thread(target=reserve, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(outcomes.count("ok"), 3)
        ledger = self.ledger()
        self.assertLessEqual(ledger.committed_twd + ledger.reserved_twd + ledger.spent_twd, ledger.limit_twd)

    def test_exhausted_budget_stops_new_generation_but_history_still_reads(self):
        from app.services import spend
        with self.factory() as db, db.begin():
            spend.ensure_month(db, spend.month_key())
            db.execute(text("UPDATE budget_months SET committed_twd = limit_twd"))
        res = self.submit()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "SERVICE_BUDGET_EXHAUSTED")
        self.assertEqual(self.client.get(f"/meal-requests/{self.request['id']}").status_code, 200)
        self.assertEqual(self.client.get("/members").status_code, 200)

    def test_scheduler_is_single_instance(self):
        from app.jobs.scheduler import acquire_singleton
        from sqlalchemy import create_engine
        from db_support import TEST_DATABASE_URL
        engine = create_engine(TEST_DATABASE_URL)
        first = acquire_singleton(engine)
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(acquire_singleton(engine))
        finally:
            first.release()
        second = acquire_singleton(engine)
        self.assertIsNotNone(second)
        second.release()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
