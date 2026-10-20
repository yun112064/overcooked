"""T17：團隊電腦離線（AC24、R11）。

以「真實」預設轉接器（依設定建立的 HttpRecognizer／HttpKnowledgeClient）指向一個沒有服務在聽的埠，
模擬團隊電腦關機或通道中斷；Access 服務權杖也照常設定。驗證：
- 辨識明確顯示不可用、照片保留可重試，不是「沒有辨識結果」；
- 菜單生成照常由雲端結構化食譜完成（食譜查詢不依賴團隊電腦），知識標示不可用而不是查無知識；
- 登入、草稿與已採用歷史等雲端功能照常可用。
"""
import shutil
import socket
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from db_support import requires_db
from test_t09_recognition import jpeg_bytes
from test_t14_generation import FakeResponder, GenerationTestCase


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"  # 關閉後沒有任何服務在聽


@requires_db
class TeamComputerOfflineTests(GenerationTestCase):
    def setUp(self):
        super().setUp()
        from knowledge_support import FakeEmbedder
        self.embedder = FakeEmbedder()
        tmp = Path(tempfile.mkdtemp(prefix="t17-offline-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        down = _closed_port_url()
        patcher = patch.multiple("app.core.config.settings", recognition_service_url=down,
                                 recognition_service_token="placeholder-token", knowledge_service_url=down,
                                 knowledge_service_token="placeholder-token", team_access_client_id="placeholder-id",
                                 team_access_client_secret="placeholder-secret", recognition_timeout_seconds=3,
                                 knowledge_timeout_seconds=3, project_data_root=tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recognition_is_unavailable_and_photo_is_kept_for_retry(self):
        res = self.client.post("/recognitions", files={"image": ("photo.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertEqual((body["status"], body["retryable"]), ("unavailable", True))
        self.assertNotEqual(body["status"], "no_result")
        self.assertEqual(self.client.get(body["image_url"]).status_code, 200)

    def test_menu_still_generated_from_cloud_recipes_with_knowledge_marked_unavailable(self):
        from app.jobs.worker import run_once
        from app.services.generation import make_generation_handler
        from sqlalchemy import text
        self.create_request(dish_count=2)
        job_id = self.submit()
        responder = FakeResponder()
        handler = make_generation_handler(responder=responder, embedder=self.embedder,
                                          calculator=self.calculator_for())  # 知識客戶端用預設（依設定）
        with self.factory() as db, db.begin():
            db.execute(text("UPDATE background_jobs SET available_at = now() WHERE status = 'queued'"))
        run_once(self.factory, "worker-a", {"meal_generation": handler})
        self.assertEqual(self.job(job_id)["status"], "succeeded")
        [row] = self.results()
        self.assertEqual(row.mode, "database_menu")
        explanation = row.payload["explanation"]
        self.assertEqual((explanation["status"], explanation["code"], explanation["reason"]),
                         ("unavailable", "KNOWLEDGE_UNAVAILABLE", "UNREACHABLE"))
        self.assertNotEqual(row.payload["knowledge"]["status"], "not_found")  # 離線 ≠ 查無知識
        self.assertEqual(responder.calls, [])

    def test_cloud_history_and_drafts_still_work(self):
        self.adopt_yesterday("番茄炒蛋")
        res = self.client.get("/meal-plans", params={"date": date(2026, 8, 19).isoformat()})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([d["dish_name"] for p in res.json()["meal_plans"] for d in p["dishes"]], ["番茄炒蛋"])
        draft = self.create_request(dish_count=1)
        self.assertEqual(self.client.get(f"/meal-requests/{draft['id']}").status_code, 200)
