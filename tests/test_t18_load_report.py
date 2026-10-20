"""T18 負載報告：p95、門檻判定與環境資料完整性（純計算，不連網路）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "load"))

import load_report as lr  # noqa: E402


def samples(category, latencies, ok=True):
    return [lr.Sample(step="s", category=category, user=i % 5, iteration=i, latency_s=v, ok=ok)
            for i, v in enumerate(latencies)]


def complete_env():
    env = lr.environment_template()

    def fill(node):
        for key, value in node.items():
            if isinstance(value, dict):
                fill(value)
            elif value is None:
                node[key] = "x"
    fill(env)
    return env


class P95Tests(unittest.TestCase):
    def test_nearest_rank_p95_of_twenty_samples_is_nineteenth_value(self):
        # 1..20：nearest-rank ceil(0.95*20)=19
        self.assertEqual(lr.p95([float(v) for v in range(20, 0, -1)]), 19.0)

    def test_p95_of_hundred_samples(self):
        self.assertEqual(lr.p95([float(v) for v in range(1, 101)]), 95.0)

    def test_p95_of_empty_is_none_not_zero(self):
        self.assertIsNone(lr.p95([]))


class ThresholdTests(unittest.TestCase):
    def test_spec_thresholds(self):
        self.assertEqual(lr.SPEC_THRESHOLDS_S, {"api": 1.0, "recognition_warm": 10.0, "generation": 90.0})

    def test_loosening_a_threshold_is_refused(self):
        with self.assertRaises(lr.ThresholdLoweringRefused):
            lr.effective_thresholds({"generation": 120})

    def test_unknown_threshold_is_refused(self):
        with self.assertRaises(lr.ThresholdLoweringRefused):
            lr.effective_thresholds({"api_p99": 5})

    def test_tightening_is_allowed(self):
        self.assertEqual(lr.effective_thresholds({"api": 0.5})["api"], 0.5)


class ReportTests(unittest.TestCase):
    def test_category_passes_when_p95_within_threshold_and_enough_samples(self):
        report = lr.build_report(samples("api", [0.2] * 30), environment=complete_env(), dry_run=False)
        api = report["categories"]["api"]
        self.assertEqual((api["verdict"], api["samples"], api["p95_s"]), ("pass", 30, 0.2))

    def test_category_fails_when_p95_over_threshold(self):
        lat = [0.2] * 18 + [1.5] * 2  # p95 = 第 19 筆 = 1.5
        report = lr.build_report(samples("api", lat), environment=complete_env(), dry_run=False)
        self.assertEqual(report["categories"]["api"]["verdict"], "fail")

    def test_failed_requests_count_against_the_category(self):
        report = lr.build_report(samples("api", [0.1] * 30) + samples("api", [0.1], ok=False),
                                 environment=complete_env(), dry_run=False)
        api = report["categories"]["api"]
        self.assertEqual((api["errors"], api["verdict"]), (1, "fail"))

    def test_too_few_samples_is_not_a_pass(self):
        report = lr.build_report(samples("api", [0.1] * 5), environment=complete_env(), dry_run=False)
        self.assertEqual(report["categories"]["api"]["verdict"], "insufficient_samples")

    def test_missing_category_has_no_samples_verdict(self):
        report = lr.build_report(samples("api", [0.1] * 30), environment=complete_env(), dry_run=False)
        self.assertEqual(report["categories"]["generation"]["verdict"], "no_samples")
        self.assertIsNone(report["categories"]["generation"]["p95_s"])

    def test_overall_pass_requires_every_category_and_full_environment(self):
        all_ok = samples("api", [0.1] * 30) + samples("recognition_warm", [3.0] * 30) + samples("generation", [40.0] * 30)
        self.assertEqual(lr.build_report(all_ok, environment=complete_env(), dry_run=False)["g06_verdict"], "pass")
        env = complete_env()
        env["team_computer"]["memory"] = None
        report = lr.build_report(all_ok, environment=env, dry_run=False)
        self.assertEqual(report["g06_verdict"], "not_verified")
        self.assertIn("team_computer.memory", report["missing_environment"])

    def test_dry_run_is_never_a_pass(self):
        all_ok = samples("api", [0.1] * 30) + samples("recognition_warm", [3.0] * 30) + samples("generation", [40.0] * 30)
        self.assertEqual(lr.build_report(all_ok, environment=complete_env(), dry_run=True)["g06_verdict"], "not_verified")

    def test_generation_is_also_broken_down_by_mode(self):
        gen = samples("generation", [10.0] * 3)
        for s in gen:
            s.detail = {"mode": "ai_generated_menu"}
        report = lr.build_report(gen, environment=complete_env(), dry_run=False)
        self.assertEqual(report["generation_by_mode"]["ai_generated_menu"]["samples"], 3)

    def test_write_report_produces_json_and_markdown(self):
        report = lr.build_report(samples("api", [0.1] * 30), environment=complete_env(), dry_run=True)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = lr.write_report(report, samples("api", [0.1] * 30), Path(tmp))
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["report"]["g06_verdict"], "not_verified")
            self.assertEqual(len(data["samples"]), 30)
            md = md_path.read_text(encoding="utf-8")
            self.assertIn("p95", md)
            self.assertIn("not_verified", md)


if __name__ == "__main__":
    unittest.main()
