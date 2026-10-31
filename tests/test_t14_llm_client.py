"""T14：OpenAI Responses API＋Structured Outputs 轉接器（以替換 httpx.post 測試，不呼叫真實 API）。"""
import json
import unittest
from unittest.mock import patch

import httpx

MODEL = "gpt-5.4-mini-2026-03-17"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
          "additionalProperties": False}


def message(content):
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content}


def body(status="completed", content=None, incomplete=None, usage=(120, 40)):
    data = {"id": "resp_1", "object": "response", "status": status, "model": MODEL,
            "output": [{"id": "rs_1", "type": "reasoning", "summary": []},
                       message(content if content is not None else
                               [{"type": "output_text", "text": json.dumps({"a": "x"})}])],
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage)}}
    if incomplete:
        data["incomplete_details"] = {"reason": incomplete}
    return data


class ResponderTests(unittest.TestCase):
    def responder(self):
        from app.services.llm_client import OpenAIResponder
        return OpenAIResponder(api_key="sk-test-not-real", base_url="https://api.openai.test/v1", timeout=5)

    def respond(self, response):
        with patch("httpx.post", return_value=response) as post:
            out = self.responder().respond(model=MODEL, instructions="系統規則", input_text="使用者資料",
                                           schema_name="menu", schema=SCHEMA, max_output_tokens=900)
        return out, post

    def test_sends_strict_json_schema_and_returns_text_with_usage(self):
        (out, usage), post = self.respond(httpx.Response(200, json=body()))
        self.assertEqual((out.status, json.loads(out.output_text)), ("completed", {"a": "x"}))
        self.assertEqual(usage, {"input_tokens": 120, "output_tokens": 40})
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.openai.test/v1/responses")
        sent = kwargs["json"]
        self.assertEqual((sent["model"], sent["instructions"], sent["input"], sent["max_output_tokens"]),
                         (MODEL, "系統規則", "使用者資料", 900))
        self.assertEqual(sent["text"]["format"],
                         {"type": "json_schema", "name": "menu", "schema": SCHEMA, "strict": True})
        self.assertIs(sent["store"], False)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test-not-real")

    def test_refusal_is_reported_not_parsed(self):
        (out, usage), _ = self.respond(httpx.Response(200, json=body(
            content=[{"type": "refusal", "refusal": "無法協助"}])))
        self.assertEqual((out.status, out.output_text, out.refusal), ("refused", None, "無法協助"))
        self.assertEqual(usage["output_tokens"], 40)

    def test_truncation_is_reported_as_incomplete(self):
        (out, _), _ = self.respond(httpx.Response(200, json=body(
            status="incomplete", incomplete="max_output_tokens",
            content=[{"type": "output_text", "text": '{"a": "x'}])))
        self.assertEqual((out.status, out.incomplete_reason), ("incomplete", "max_output_tokens"))

    def test_rejected_or_unsent_requests_are_not_billed(self):
        from app.services import spend
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with self.assertRaises(spend.CallNotBilled):
                self.responder().respond(model=MODEL, instructions="", input_text="", schema_name="m",
                                         schema=SCHEMA, max_output_tokens=10)
        with self.assertRaises(spend.CallNotBilled):
            self.respond(httpx.Response(400, json={"error": {"message": "bad schema"}}))

    def test_server_error_or_malformed_body_is_uncertain(self):
        from app.services import spend
        for response in (httpx.Response(500, json={}), httpx.Response(200, json={"status": "completed"})):
            with self.assertRaises(Exception) as caught:
                self.respond(response)
            self.assertNotIsInstance(caught.exception, spend.CallNotBilled)

    def test_unset_api_key_means_no_responder(self):
        from app.core.config import settings
        from app.services.llm_client import default_responder
        with patch.object(settings, "openai_api_key", None):
            self.assertIsNone(default_responder())
        with patch.object(settings, "openai_api_key", "sk-test-not-real"):
            self.assertIsNotNone(default_responder())

    def test_generation_model_is_the_pinned_priced_model(self):
        from app.core.config import settings
        from app.services import spend
        self.assertEqual(settings.generation_model, MODEL)
        self.assertGreater(spend.cost_twd(settings.generation_model, 1000, 1000), 0)


if __name__ == "__main__":
    unittest.main()
