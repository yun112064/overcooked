"""T18 負載腳本對真實後端 API 的請求形狀檢查（程序內 ASGI，假辨識器，無 worker、無付費呼叫）。

沒有 worker，生成工作不會完成；這裡只確認腳本送出的每個請求被真實 API 接受。
"""
import asyncio
import io
import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "load"))

from db_support import ORIGIN, make_client, migrate_fresh, requires_db, seed_foods, truncate_all  # noqa: E402

SCENARIO = Path(__file__).resolve().parents[1] / "scripts" / "load" / "scenario.example.json"


class FakeRecognizer:
    def detect(self, jpeg):
        from app.services.recognition import Detection, RecognizerOutput
        return RecognizerOutput("t18-fake", [Detection("高麗菜", 0.9, [0, 0, 10, 10])])


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), (0, 160, 0)).save(buf, "JPEG")
    return buf.getvalue()


@requires_db
class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        from app.api.recognitions import get_recognizer
        from app.main import app
        self.app, self.get_recognizer = app, get_recognizer
        app.dependency_overrides[get_recognizer] = lambda: FakeRecognizer()
        make_client()  # 設定 get_db 覆寫到測試資料庫

    def tearDown(self):
        self.app.dependency_overrides.pop(self.get_recognizer, None)

    def test_flow_requests_are_accepted_by_the_real_api(self):
        import five_user_load as fl
        scenario = fl.load_scenario(SCENARIO)
        result = asyncio.run(fl.run_load(
            scenario, transport=fl.asgi_transport(self.app), base_url="https://testserver", origin=ORIGIN,
            iterations=1, poll_interval_s=0.05, timeout_s=0.5, image=jpeg()))
        by_step = {}
        for s in result.samples:
            by_step.setdefault(s.step, []).append(s)
        for step in ("login", "household", "member_create", "recognition_warmup", "me", "questionnaire_create",
                     "questionnaire_read", "recognition", "recognition_confirm", "previous_day_read",
                     "generation_submit", "job_poll", "fallback_questionnaire_create", "fallback_submit"):
            self.assertIn(step, by_step)
            bad = [(s.status_code, s.detail) for s in by_step[step] if not s.ok]
            self.assertEqual(bad, [], step)
        self.assertEqual(len(by_step["recognition"]), 5)
        self.assertTrue(all(s.detail["recognition_status"] == "succeeded" for s in by_step["recognition"]))
        # 沒有 worker：生成在逾時後記為失敗樣本，而不是被略過
        gen = by_step["generation_database"]
        self.assertTrue(gen and all(s.detail.get("error") == "TIMEOUT" for s in gen))


if __name__ == "__main__":
    unittest.main()
