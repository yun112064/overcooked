"""T18 五人負載腳本：以 dry-run 對本機假伺服器驗證流程、計時與報告（不連真實服務）。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "load"))

import fake_server  # noqa: E402
import five_user_load as fl  # noqa: E402

SCENARIO = Path(__file__).resolve().parents[1] / "scripts" / "load" / "scenario.example.json"


def run(app, **kw):
    scenario = fl.load_scenario(SCENARIO)
    kw.setdefault("users", 5)
    kw.setdefault("iterations", 1)
    return asyncio.run(fl.run_load(scenario, transport=fl.asgi_transport(app), base_url="https://fake.test",
                                   poll_interval_s=0, **kw))


class FlowTests(unittest.TestCase):
    def test_every_user_runs_the_whole_flow(self):
        app = fake_server.create_app()
        result = run(app)
        steps = {s.step for s in result.samples}
        for step in fl.FLOW_STEPS:
            self.assertIn(step, steps)
        users = {s.user for s in result.samples}
        self.assertEqual(users, set(range(5)))
        self.assertTrue(all(s.ok for s in result.samples), [s for s in result.samples if not s.ok][:3])

    def test_both_generation_modes_are_measured(self):
        result = run(fake_server.create_app())
        modes = {s.detail.get("mode") for s in result.samples if s.category == "generation"}
        self.assertEqual(modes, {"database_menu", "ai_generated_menu"})
        self.assertEqual(result.issues, [])

    def test_five_users_really_overlap(self):
        app = fake_server.create_app(delay_s=0.02)
        run(app)
        self.assertGreaterEqual(app.state.fake.max_in_flight, 5)

    def test_generation_latency_includes_queue_time(self):
        app = fake_server.create_app(polls_until_done=3, delay_s=0.01)
        result = run(app)
        gen = [s for s in result.samples if s.category == "generation"]
        # 送出到完成需經過多次輪詢，因此生成時間必大於單一請求
        submit = [s for s in result.samples if s.step == "generation_submit"]
        self.assertGreater(min(s.latency_s for s in gen), max(s.latency_s for s in submit))
        self.assertTrue(all(s.detail["polls"] >= 3 for s in gen))

    def test_warm_up_recognition_is_not_counted(self):
        result = run(fake_server.create_app())
        warm = [s for s in result.samples if s.category == "recognition_warm"]
        self.assertEqual(len(warm), 5)
        self.assertTrue(all(s.step == "recognition" for s in warm))
        self.assertEqual(sum(1 for s in result.samples if s.step == "recognition_warmup"), 1)

    def test_fallback_that_does_not_trigger_ai_is_reported(self):
        result = run(fake_server.create_app(fallback_works=False))
        self.assertTrue(any(i["code"] == "FALLBACK_NOT_TRIGGERED" for i in result.issues))

    def test_failed_generation_is_an_error_sample(self):
        result = run(fake_server.create_app(fail_generation=True))
        gen = [s for s in result.samples if s.category == "generation"]
        self.assertTrue(gen and not any(s.ok for s in gen))

    def test_knowledge_and_previous_day_status_are_recorded(self):
        result = run(fake_server.create_app())
        view = next(s for s in result.samples if s.step == "result_view")
        self.assertIn("knowledge_status", view.detail)
        recheck = next(s for s in result.samples if s.step == "previous_day_read")
        self.assertIn("count", recheck.detail)

    def test_fewer_than_five_users_is_refused(self):
        with self.assertRaises(ValueError):
            run(fake_server.create_app(), users=4)


class CliTests(unittest.TestCase):
    def test_dry_run_writes_report_and_is_never_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = fl.main(["--dry-run", "--scenario", str(SCENARIO), "--out", tmp, "--iterations", "4"])
            self.assertEqual(code, 0)
            files = sorted(Path(tmp).iterdir())
            self.assertEqual([f.suffix for f in files], [".json", ".md"])
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertTrue(data["report"]["dry_run"])
            self.assertEqual(data["report"]["g06_verdict"], "not_verified")
            self.assertEqual(data["report"]["categories"]["api"]["verdict"], "pass")

    def test_cli_refuses_loosened_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = fl.main(["--dry-run", "--scenario", str(SCENARIO), "--out", tmp,
                            "--threshold", "api=2"])
            self.assertEqual(code, 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_real_run_requires_base_url(self):
        self.assertEqual(fl.main(["--scenario", str(SCENARIO)]), 2)


if __name__ == "__main__":
    unittest.main()
