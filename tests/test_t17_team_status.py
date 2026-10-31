"""pre-review #5：團隊電腦服務狀態（在線／離線／未設定），供畫面提示與 QA；不呼叫真實服務。"""
import unittest
from unittest.mock import patch

import httpx

URL = "https://knowledge.team.example.invalid"


class TeamStatusTests(unittest.TestCase):
    def setUp(self):
        from app.services import team_status
        team_status._cache.clear()

    def probe(self, response=None, error=None):
        from app.services.team_link import TeamCredentials
        from app.services.team_status import probe
        calls = []

        def get(url, **kwargs):
            calls.append((url, kwargs["headers"]))
            if error:
                raise error
            return response
        return probe(URL, TeamCredentials("tok", "cid", "csec"), get=get), calls

    def test_online_offline_and_not_configured_are_distinguished(self):
        from app.services.team_link import TeamCredentials
        from app.services.team_status import probe
        req = httpx.Request("GET", URL)
        state, calls = self.probe(httpx.Response(200, request=req, json={"status": "ok"}))
        self.assertEqual(state, {"status": "online", "reason": None})
        self.assertEqual(calls[0][0], URL + "/v1/health")
        self.assertEqual(calls[0][1]["CF-Access-Client-Id"], "cid")
        self.assertEqual(self.probe(httpx.Response(530, request=req))[0], {"status": "offline", "reason": "UNREACHABLE"})
        self.assertEqual(self.probe(httpx.Response(503, request=req))[0],
                         {"status": "offline", "reason": "SERVICE_NOT_READY"})
        self.assertEqual(self.probe(error=httpx.ConnectTimeout("x"))[0], {"status": "offline", "reason": "TIMEOUT"})
        self.assertEqual(probe(None, TeamCredentials(None))["status"], "not_configured")

    def test_results_are_cached_briefly(self):
        from app.services.team_status import team_status
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url))
        clock = iter([0.0, 0.0, 10.0, 10.0, 40.0, 40.0, 40.0, 40.0])
        with patch.multiple("app.core.config.settings", recognition_service_url=None, knowledge_service_url=URL):
            for _ in range(3):
                status = team_status(now=lambda: next(clock), get=get)
        self.assertEqual(status["knowledge"]["status"], "online")
        self.assertEqual(status["recognition"]["status"], "not_configured")
        self.assertEqual(len(calls), 2)  # 0 秒與 40 秒各探測一次；10 秒時用快取


if __name__ == "__main__":
    unittest.main()
