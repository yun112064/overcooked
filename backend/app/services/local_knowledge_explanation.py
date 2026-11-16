"""Opt-in local structured knowledge wording; no paid-provider fallback."""
import httpx

from app.services.llm_client import StructuredResponse
from app.services.knowledge_deadline import LocalKnowledgeTimeout, bounded_local_call, local_timeout


class LocalExplanationUnavailable(Exception):
    pass


class OllamaKnowledgeResponder:
    provider = 'ollama'

    def __init__(self, url, model, *, timeout=120.0, transport=None):
        self.url, self.model = url.rstrip('/'), model
        self.timeout, self.transport = timeout, transport

    def respond(self, *, instructions, input_text, schema, max_output_tokens):
        payload = {
            'model': self.model, 'stream': False, 'think': False,
            'messages': [{'role': 'system', 'content': instructions},
                         {'role': 'user', 'content': input_text}],
            'format': schema, 'keep_alive': '10m',
            'options': {'temperature': 0, 'num_predict': max_output_tokens},
        }
        try:
            timeout = local_timeout(self.timeout)
            def request():
                with httpx.Client(timeout=timeout, transport=self.transport) as client:
                    response = client.post(self.url + '/api/chat', json=payload)
                    response.raise_for_status()
                    return response.json()
            body = bounded_local_call(request, timeout)
            text = (body.get('message') or {}).get('content')
            if (body.get('error') or body.get('done') is not True
                    or body.get('done_reason') not in ('stop', None)
                    or not isinstance(text, str) or not text.strip()):
                raise ValueError('Incomplete local response')
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, LocalKnowledgeTimeout):
            raise LocalExplanationUnavailable('LOCAL_EXPLANATION_UNAVAILABLE') from None
        # Citation/grounding/schema checks remain the responsibility of each caller.
        return StructuredResponse('completed', output_text=text)


def configured_local_responder():
    from app.core.config import settings
    if settings.knowledge_explain_provider != 'ollama' or not settings.knowledge_ollama_url:
        return None
    return OllamaKnowledgeResponder(settings.knowledge_ollama_url,
        settings.knowledge_ollama_model, timeout=settings.knowledge_ollama_timeout_seconds)
