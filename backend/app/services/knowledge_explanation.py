"""T11／T14 交接：最終食材的營養小知識文字改由雲端 OpenAI 生成（資料仍為團隊電腦的審核知識）。

流程：retrieve_knowledge（T11，地端 pgvector 檢索 Excel 匯入的審核知識）→ 每個有知識的食材呼叫一次 OpenAI
Responses API＋Structured Outputs（提示詞與驗證見 knowledge_rag.explain）→ 程式驗證後才可顯示。

- 每次呼叫經 T10 預留／結算：paid_call 與 JobContext.paid_call 同介面（name=...）。
- 驗證不通過的說明不顯示，記在 dropped；缺知識的食材省略（not_found），不阻擋採用。
- 知識服務不可用、額度不足或模型失敗：該食材 unavailable，菜單照常，可之後只重試說明。
- 不記錄金鑰、輸入或模型原始回應。
"""
import time

import httpx

from app.services import spend
from knowledge_rag.explain import INSTRUCTIONS, PROMPT_VERSION, SCHEMA, build_input, parse_output, validate

DEFAULT_MODEL = 'gpt-5.4-mini-2026-03-17'
MAX_OUTPUT_TOKENS = 600


class OpenAIExplainer:
    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, base_url: str = 'https://api.openai.com/v1',
                 timeout: float = 60.0):
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url.rstrip('/'), timeout

    def respond(self, input_text: str):
        payload = {'model': self.model, 'instructions': INSTRUCTIONS, 'input': input_text, 'store': False,
                   'max_output_tokens': MAX_OUTPUT_TOKENS,
                   'text': {'format': {'type': 'json_schema', 'name': 't11_tip', 'schema': SCHEMA, 'strict': True}}}
        try:
            res = httpx.post(f'{self.base_url}/responses', json=payload, timeout=self.timeout,
                             headers={'Authorization': f'Bearer {self.api_key}'})
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise spend.CallNotBilled('connect failed') from None
        if 400 <= res.status_code < 500 and res.status_code != 408:
            raise spend.CallNotBilled(f'HTTP {res.status_code}')
        if res.status_code != 200:
            raise ValueError(f'HTTP {res.status_code}')  # 可能已計費 → uncertain
        body = res.json()
        usage = {'input_tokens': int(body['usage']['input_tokens']), 'output_tokens': int(body['usage']['output_tokens'])}
        content = [c for item in body.get('output') or [] if item.get('type') == 'message'
                   for c in item.get('content') or []]
        if any(c.get('type') == 'refusal' for c in content) or body.get('status') != 'completed':
            return None, usage
        return ''.join(c.get('text') or '' for c in content if c.get('type') == 'output_text'), usage


def explain_snapshot(paid_call, explainer, snapshot: dict, names: dict, *, call_prefix: str = 'explain') -> dict:
    """snapshot：T11 retrieve_knowledge 的結果；names：{食材ID: 名稱}。"""
    if snapshot.get('status') == 'unavailable':
        return {'status': 'unavailable', 'reason': snapshot.get('reason'), 'items': [], 'dropped': []}
    if explainer is None:
        return {'status': 'unavailable', 'reason': 'EXPLANATION_NOT_CONFIGURED', 'items': [], 'dropped': []}
    by_food = {}
    for r in snapshot.get('records') or []:
        by_food.setdefault(r['ingredient_id'], []).append(r)
    items, dropped = [], []
    for food_id, records in sorted(by_food.items()):
        input_text = build_input(names.get(food_id) or food_id, records)
        started = time.monotonic()
        try:
            text = paid_call(name=f'{call_prefix}:{food_id}', purpose='knowledge_explanation', provider='openai',
                             model=explainer.model, max_input_tokens=len((INSTRUCTIONS + input_text).encode()) + 400,
                             max_output_tokens=MAX_OUTPUT_TOKENS, call=lambda: explainer.respond(input_text))
        except spend.CallNotBilled:
            dropped.append({'ingredient_id': food_id, 'reason': 'EXPLANATION_UNAVAILABLE'})
            continue
        try:
            result = validate(parse_output(text), records) if text else validate(None, records)
        except (ValueError, TypeError):
            result = {'status': 'rejected', 'failures': ['INVALID_FORMAT']}
        if result['status'] != 'accepted':
            dropped.append({'ingredient_id': food_id, 'reason': ','.join(result['failures'])})
            continue
        cited = {r['knowledge_id']: r for r in records}
        items.append({'ingredient_id': food_id, 'ingredient_name': names.get(food_id), 'tip_zh': result['tip_zh'],
                      'prompt_version': PROMPT_VERSION, 'model': explainer.model,
                      'latency_ms': int((time.monotonic() - started) * 1000),
                      'citations': [{k: cited[c].get(k) for k in ('knowledge_id', 'source_version', 'ingredient_id',
                                                                  'source_url', 'conditions')}
                                    for c in result['citations']]})
    return {'status': 'available' if items else 'not_found', 'items': items, 'dropped': dropped,
            'not_found': [i for i, s in (snapshot.get('by_ingredient') or {}).items() if s == 'not_found']}
