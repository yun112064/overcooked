"""R14 飲食小幫手 OpenAI 版（假 OpenAI、假額度控制；不連資料庫、不連網、不花錢）。"""
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.api import internal_assistant  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import spend  # noqa: E402
from app.services.llm_client import OpenAIResponder, StructuredResponse  # noqa: E402

MESSAGES = [{"role": "system", "content": "只能依參考資料回答。"}, {"role": "user", "content": "吃地瓜有什麼好處"}]


class FakeResponder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or StructuredResponse("completed", output_text="地瓜富含膳食纖維 [1]。")

    def chat_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.result, {"input_tokens": 120, "output_tokens": 30}


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "internal-secret")
    paid = []

    def fake_paid_call(factory, **kwargs):
        paid.append(kwargs)
        result, usage = kwargs["call"]()
        return result

    monkeypatch.setattr(spend, "paid_call", fake_paid_call)
    responder = FakeResponder()
    app = FastAPI()
    from app.api.errors import register_error_handlers
    register_error_handlers(app)
    app.include_router(internal_assistant.router)
    app.dependency_overrides[internal_assistant.get_session_factory] = lambda: "factory"
    app.dependency_overrides[internal_assistant.get_assistant_responder] = lambda: responder
    client = TestClient(app)
    return SimpleNamespace(client=client, paid=paid, responder=responder, app=app)


def post(internal, body=None, token="internal-secret"):
    return internal.client.post("/internal/assistant/chat", json=body or {"call_id": "abc12345", "messages": MESSAGES},
                                headers={"Authorization": f"Bearer {token}"})


def test_generates_through_t10_paid_call(internal):
    res = post(internal)
    assert res.status_code == 200, res.text
    assert res.json() == {"model": settings.assistant_openai_model, "text": "地瓜富含膳食纖維 [1]。"}
    [paid] = internal.paid
    assert paid["call_key"] == "assistant:abc12345"
    assert paid["purpose"] == "assistant_answer" and paid["provider"] == "openai"
    assert paid["model"] == settings.assistant_openai_model
    # 預留上限：每字 2 token＋每則 20＋50，必須不小於實際用量
    chars = sum(len(m["content"]) for m in MESSAGES)
    assert paid["max_input_tokens"] == chars * 2 + 20 * len(MESSAGES) + 50
    assert paid["max_output_tokens"] == settings.assistant_openai_max_output_tokens
    assert internal.responder.calls[0]["messages"] == MESSAGES


def test_wrong_or_missing_token_is_rejected(internal, monkeypatch):
    assert post(internal, token="nope").status_code == 401
    monkeypatch.setattr(settings, "internal_api_token", None)
    assert post(internal).status_code == 503
    assert internal.paid == []


def test_budget_exhausted_and_not_configured(internal, monkeypatch):
    def exhausted(factory, **kwargs):
        raise spend.BudgetExhausted()
    monkeypatch.setattr(spend, "paid_call", exhausted)
    res = post(internal)
    assert res.status_code == 503 and res.json()["error"]["code"] == "SERVICE_BUDGET_EXHAUSTED"
    internal.app.dependency_overrides[internal_assistant.get_assistant_responder] = lambda: None
    res = post(internal)
    assert res.status_code == 503 and res.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def test_incomplete_or_refused_output_is_not_returned(internal):
    internal.responder.result = StructuredResponse("incomplete", incomplete_reason="max_output_tokens")
    res = post(internal)
    assert res.status_code == 502 and res.json()["error"]["code"] == "LLM_INCOMPLETE"
    internal.responder.result = StructuredResponse("refused", refusal="no")
    assert post(internal).status_code == 502


def test_rejects_bad_call_id_and_roles(internal):
    assert post(internal, {"call_id": "bad id!", "messages": MESSAGES}).status_code == 422
    assert post(internal, {"call_id": "abc12345", "messages": [{"role": "tool", "content": "x"}]}).status_code == 422
    assert internal.paid == []


def test_openai_responder_chat_text_payload(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(200, json={
            "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "好"}]}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    responder = OpenAIResponder(api_key="sk-test", base_url="https://api.example.test/v1", timeout=5)
    result, usage = responder.chat_text(model="m", messages=MESSAGES, max_output_tokens=100, json_mode=True)
    assert (result.status, result.output_text, usage) == ("completed", "好", {"input_tokens": 10, "output_tokens": 5})
    assert seen["url"] == "https://api.example.test/v1/responses"
    assert seen["json"]["input"] == MESSAGES and seen["json"]["store"] is False
    assert seen["json"]["text"] == {"format": {"type": "json_object"}}


# ---- 雲端 /assistant/chat 依 engine 轉送到地端或 OpenAI 版服務 ----

def test_chat_engine_selects_service():
    from test_assistant_proxy import SSE, client_for, proxy_with
    from app.api import assistant as api
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE)

    client = client_for(proxy_with(handler, url="http://local.test"))
    client.app.dependency_overrides[api.get_openai_proxy] = lambda: proxy_with(handler, url="http://openai.test")
    assert client.post("/assistant/chat", json={"message": "地瓜"}).status_code == 200
    assert client.post("/assistant/chat", json={"message": "地瓜", "engine": "openai"}).status_code == 200
    assert urls == ["http://local.test/v1/chat", "http://openai.test/v1/chat"]
    assert client.post("/assistant/chat", json={"message": "地瓜", "engine": "gpt"}).status_code == 422


def test_openai_service_not_configured_is_unavailable():
    from test_assistant_proxy import client_for, proxy_with
    from app.api import assistant as api
    from app.services import assistant as service
    client = client_for(proxy_with(lambda r: httpx.Response(500)))
    client.app.dependency_overrides[api.get_openai_proxy] = lambda: service.AssistantProxy(None, None)
    res = client.post("/assistant/chat", json={"message": "地瓜", "engine": "openai"})
    assert res.status_code == 503 and res.json()["error"]["details"]["reason"] == "NOT_CONFIGURED"


def test_team_status_reports_openai_copy(monkeypatch):
    from app.services import team_status
    monkeypatch.setattr(settings, "assistant_openai_service_url", None)
    team_status._cache.clear()
    out = team_status.team_status(get=lambda *a, **k: httpx.Response(200))
    team_status._cache.clear()
    assert out["assistant_openai"]["status"] == "not_configured"


# ---- 團隊電腦端：CloudOpenAIChat ----

def cloud_chat(handler, monkeypatch, **env):
    from assistant_rag.providers import CloudOpenAIChat
    for key in ("CLOUD_ACCESS_CLIENT_ID", "CLOUD_ACCESS_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return CloudOpenAIChat("https://cloud-internal.test/", "tok", transport=httpx.MockTransport(handler))


def test_cloud_chat_sends_credentials_and_unique_call_ids(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.path, dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={"model": "m", "text": "第一句。第二句！\n第三句"})

    chat = cloud_chat(handler, monkeypatch, CLOUD_ACCESS_CLIENT_ID="cf-id", CLOUD_ACCESS_CLIENT_SECRET="cf-secret")
    assert chat.complete(MESSAGES, json_mode=True) == "第一句。第二句！\n第三句"
    assert list(chat.stream(MESSAGES)) == ["第一句。", "第二句！", "\n", "第三句"]
    (path, headers, body), (_, _, body2) = seen
    assert path == "/internal/assistant/chat"
    assert headers["authorization"] == "Bearer tok"
    assert headers["cf-access-client-id"] == "cf-id" and headers["cf-access-client-secret"] == "cf-secret"
    assert body["messages"] == MESSAGES and body["json_mode"] is True and body2["json_mode"] is False
    assert body["call_id"] != body2["call_id"]


def test_cloud_chat_errors_map_to_provider_codes(monkeypatch):
    from assistant_rag.providers import CloudOpenAIChat, ProviderUnavailable
    budget = cloud_chat(lambda r: httpx.Response(503, json={"error": {"code": "SERVICE_BUDGET_EXHAUSTED"}}), monkeypatch)
    with pytest.raises(ProviderUnavailable) as exc:
        budget.complete(MESSAGES)
    assert exc.value.code == "LLM_BUDGET_EXHAUSTED"
    with pytest.raises(ProviderUnavailable) as exc:
        cloud_chat(lambda r: httpx.Response(401, json={}), monkeypatch).complete(MESSAGES)
    assert exc.value.code == "LLM_AUTH_FAILED"
    monkeypatch.delenv("CLOUD_INTERNAL_URL", raising=False)
    monkeypatch.delenv("CLOUD_INTERNAL_TOKEN", raising=False)
    with pytest.raises(ProviderUnavailable) as exc:
        CloudOpenAIChat().complete(MESSAGES)
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_chat_model_switch(monkeypatch):
    from assistant_rag.providers import CloudOpenAIChat, OllamaChat, chat_model
    monkeypatch.setenv("RAG_LLM_PROVIDER", "openai")
    assert isinstance(chat_model(), CloudOpenAIChat)
    monkeypatch.setenv("RAG_LLM_PROVIDER", "ollama")
    assert isinstance(chat_model(), OllamaChat)
