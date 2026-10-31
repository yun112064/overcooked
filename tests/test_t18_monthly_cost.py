"""T18 每月總費用試算（AC25）：Decimal 計算、缺值不當 0、NT$2,000 上限不可放寬。"""
import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "load"))

import monthly_cost as mc  # noqa: E402

PRICING = {"pricing_version": "test-v1", "currency": "USD", "models": {
    "gpt-5.4-mini-2026-03-17": {"input": "0.75", "output": "4.50"},
    "text-embedding-3-small": {"input": "0.02", "output": "0"}}}


def quote():
    def item(category, **kw):
        return {"category": category, "name": category, "source": "quote-123", "checked_on": "2026-10-20", **kw}
    return {
        "quote_date": "2026-10-20",
        "fx": {"USD_TWD": "32", "source": "bank", "checked_on": "2026-10-20"},
        "items": [
            item("host", currency="USD", billing="monthly", amount="12", tax_rate="0.05"),
            item("data", currency="TWD", billing="monthly", amount="100", tax_rate="0"),
            item("backup", currency="TWD", billing="yearly", amount="600", tax_rate="0"),
            item("model", currency="USD", billing="tokens", model="gpt-5.4-mini-2026-03-17",
                 monthly_input_tokens="10000000", monthly_output_tokens="2000000", tax_rate="0"),
            item("embedding", currency="USD", billing="tokens", model="text-embedding-3-small",
                 monthly_input_tokens="5000000", monthly_output_tokens="0", tax_rate="0"),
        ],
    }


class MonthlyCostTests(unittest.TestCase):
    def test_worked_example_within_cap(self):
        # host 12×1.05×32=403.20；data 100；backup 600/12=50；model (7.5+9)×32=528；embedding 0.1×32=3.20
        r = mc.calculate(quote(), PRICING)
        self.assertEqual(r["status"], "within_cap")
        self.assertEqual(r["total_twd"], Decimal("1084.40"))
        self.assertEqual(r["by_category"]["model"], Decimal("528.00"))
        self.assertEqual(r["fixed_twd"], Decimal("553.20"))
        self.assertEqual(r["headroom_twd"], Decimal("915.60"))
        self.assertEqual(r["pricing_version"], "test-v1")

    def test_over_cap(self):
        q = quote()
        q["items"][0]["amount"] = "60"  # 60×1.05×32 = 2016
        r = mc.calculate(q, PRICING)
        self.assertEqual(r["status"], "over_cap")
        self.assertLess(r["headroom_twd"], 0)

    def test_usage_billing(self):
        q = quote()
        q["items"][1] = {"category": "data", "name": "流量", "currency": "TWD", "billing": "usage",
                         "unit_price": "2.5", "monthly_quantity": "40", "tax_rate": "0", "source": "q", "checked_on": "d"}
        self.assertEqual(mc.calculate(q, PRICING)["by_category"]["data"], Decimal("100.00"))

    def test_missing_value_is_incomplete_not_zero(self):
        q = quote()
        q["items"][0]["amount"] = None
        r = mc.calculate(q, PRICING)
        self.assertEqual(r["status"], "incomplete")
        self.assertIsNone(r["total_twd"])
        self.assertIn("items[0].amount", r["missing"])

    def test_missing_fx_is_incomplete(self):
        q = quote()
        q["fx"]["USD_TWD"] = None
        self.assertIn("fx.USD_TWD", mc.calculate(q, PRICING)["missing"])

    def test_missing_tax_rate_is_incomplete(self):
        q = quote()
        del q["items"][2]["tax_rate"]
        self.assertIn("items[2].tax_rate", mc.calculate(q, PRICING)["missing"])

    def test_every_required_category_must_be_quoted(self):
        q = quote()
        q["items"] = [i for i in q["items"] if i["category"] != "backup"]
        r = mc.calculate(q, PRICING)
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("category:backup", r["missing"])

    def test_unpriced_model_is_refused(self):
        q = quote()
        q["items"][3]["model"] = "gpt-unknown"
        r = mc.calculate(q, PRICING)
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("items[3].model", r["missing"])

    def test_cap_cannot_be_raised(self):
        with self.assertRaises(mc.CapRaiseRefused):
            mc.calculate(quote(), PRICING, cap_twd=Decimal("2500"))

    def test_template_is_incomplete_until_filled(self):
        template = json.loads(mc.TEMPLATE.read_text(encoding="utf-8"), parse_float=Decimal)
        r = mc.calculate(template, mc.load_pricing())
        self.assertEqual(r["status"], "incomplete")
        self.assertTrue(r["missing"])

    def test_cli_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / "quote.json"
            qpath.write_text(json.dumps(quote()), encoding="utf-8")
            out = Path(tmp) / "out"
            self.assertEqual(mc.main([str(qpath), "--out", str(out)]), 0)
            files = sorted(out.iterdir())
            self.assertEqual([f.suffix for f in files], [".json", ".md"])
            self.assertIn("within_cap", files[1].read_text(encoding="utf-8"))

    def test_input_quote_is_not_mutated(self):
        q = quote()
        before = copy.deepcopy(q)
        mc.calculate(q, PRICING)
        self.assertEqual(q, before)


if __name__ == "__main__":
    unittest.main()
