"""T17：雲端→團隊電腦連線（Cloudflare Tunnel＋Access 服務權杖＋服務 Bearer token）。

兩個轉接器（YOLO 辨識、知識 RAG）都要同時帶 Access 服務權杖標頭與服務自己的 Bearer token；
Access 拒絕（403 或導向登入頁）與通道中斷（Cloudflare 530／502／504）都必須是明確的
「服務不可用」，絕不能被當成「沒有辨識結果」或「查無知識」。不呼叫任何真實外部服務。
"""
import unittest
from unittest.mock import patch

import httpx

URL = "https://knowledge.team.example.invalid"
ACCESS = {"CF-Access-Client-Id": "placeholder-id.access", "CF-Access-Client-Secret": "placeholder-secret"}


def _recognizer(**kw):
    from app.services.recognition import HttpRecognizer
    return HttpRecognizer("https://yolo.team.example.invalid", "svc-token", 1, **kw)


def _knowledge(**kw):
    from app.services.knowledge import HttpKnowledgeClient
    return HttpKnowledgeClient(URL, "svc-token", 1, **kw)


def _recognize(client):
    return client.detect(b"jpeg")


def _search(client):
    return client.search(["FOOD000867"], [0.0] * 3, "text-embedding-3-small", limit_per_ingredient=3)


GOOD = {
    "recognition": {"model_version": "v1", "detections": []},
    "knowledge": {"records": [], "index_version": "idx-1", "embedding_model": "text-embedding-3-small"},
}


class AccessHeaderTests(unittest.TestCase):
    def cases(self):
        from app.services.knowledge import KnowledgeUnavailable
        from app.services.recognition import RecognizerUnavailable
        return [("recognition", _recognizer, _recognize, RecognizerUnavailable),
                ("knowledge", _knowledge, _search, KnowledgeUnavailable)]

    def test_both_adapters_send_access_service_token_and_bearer(self):
        for name, make, call, _ in self.cases():
            with self.subTest(name), patch("httpx.post", return_value=httpx.Response(
                    200, json=GOOD[name], request=httpx.Request("POST", URL))) as post:
                call(make(access_client_id=ACCESS["CF-Access-Client-Id"],
                          access_client_secret=ACCESS["CF-Access-Client-Secret"]))
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer svc-token", **ACCESS})

    def test_access_headers_are_omitted_when_not_configured(self):
        for name, make, call, _ in self.cases():
            with self.subTest(name), patch("httpx.post", return_value=httpx.Response(
                    200, json=GOOD[name], request=httpx.Request("POST", URL))) as post:
                call(make())
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer svc-token"})

    def test_access_rejection_and_tunnel_outage_are_explicitly_unavailable(self):
        req = httpx.Request("POST", URL)
        outcomes = [
            (httpx.Response(403, request=req), "AUTH_FAILED"),                       # 服務權杖錯誤
            (httpx.Response(302, request=req, headers={"Location": "https://team.cloudflareaccess.com/login"}),
             "AUTH_FAILED"),                                                         # 缺服務權杖 → 導向登入
            (httpx.Response(200, request=req, content=b"<html>Sign in</html>"), "INVALID_RESPONSE"),
            (httpx.Response(530, request=req), "UNREACHABLE"),                       # 團隊電腦關機／通道斷線
            (httpx.Response(502, request=req), "UNREACHABLE"),
            (httpx.Response(504, request=req), "UNREACHABLE"),
        ]
        for name, make, call, error in self.cases():
            for response, reason in outcomes:
                with self.subTest(name, status=response.status_code), patch("httpx.post", return_value=response):
                    with self.assertRaises(error) as ctx:
                        call(make(access_client_id="id", access_client_secret="secret"))
                    self.assertEqual(ctx.exception.reason, reason)

    def test_default_adapters_take_access_token_from_settings(self):
        from app.services.knowledge import default_knowledge_client
        from app.services.recognition import default_recognizer
        with patch.multiple("app.core.config.settings", recognition_service_url="https://yolo.example.invalid",
                            recognition_service_token="r", knowledge_service_url=URL, knowledge_service_token="k",
                            team_access_client_id="cid", team_access_client_secret="csecret"):
            for client in (default_recognizer(), default_knowledge_client()):
                self.assertEqual((client.access_client_id, client.access_client_secret), ("cid", "csecret"))


if __name__ == "__main__":
    unittest.main()
