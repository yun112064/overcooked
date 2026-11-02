"""T11 最後營養小知識文字改由雲端 OpenAI 生成（資料仍為團隊電腦的審核知識）。

流程：T12 最終食材（後端自行重算，不信任前端）→ T11 retrieve_knowledge（地端 pgvector 檢索 Excel 匯入的審核知識）
→ 每個有知識的食材呼叫一次 OpenAI Responses API＋Structured Outputs（提示詞與驗證見 knowledge_rag.explain，
與地端 Qwen 共用）→ 程式驗證通過才回傳。

- 每次付費呼叫經 T10 預留／結算：paid_call 與 JobContext.paid_call 同介面（name=...）；同步 API 以
  request_paid_call 包成同介面，呼叫鍵含本次請求的隨機 ID，重送不會撞鍵。
- 多個食材並行生成（MAX_PARALLEL），總時間約等於最慢的一個。
- 驗證不通過不顯示，記在 dropped；缺知識的食材在 not_found，不阻擋採用。
- 知識服務不可用、額度不足、計費不明或模型失敗：回 unavailable／dropped 並附原因，菜單照常，可稍後重試。
- 不記錄金鑰、輸入或模型原始回應。
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.services import spend
from knowledge_rag.explain import INSTRUCTIONS, PROMPT_VERSION, SCHEMA, build_input, parse_output, validate

DEFAULT_MODEL = 'gpt-5.4-mini-2026-03-17'
MAX_OUTPUT_TOKENS = 600
MAX_PARALLEL = 4
BILLING_STOPS = {spend.BudgetExhausted: 'BUDGET_EXHAUSTED', spend.UncertainBilling: 'BILLING_UNCERTAIN'}


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


def default_explainer():
    from app.core.config import settings
    if not settings.openai_api_key:
        return None
    return OpenAIExplainer(settings.openai_api_key, model=settings.knowledge_explain_model)


def request_paid_call(factory, *, request_tag: str, household_id=None):
    """同步 API 用：包成 JobContext.paid_call 介面；每次 HTTP 請求一個新的隨機 ID，呼叫鍵不重複。"""
    attempt = uuid.uuid4().hex[:12]

    def paid_call(*, name, purpose, provider, model, max_input_tokens, max_output_tokens, call):
        return spend.paid_call(factory, call_key=f'{request_tag}:{attempt}:{name}'[:150], purpose=purpose,
                               provider=provider, model=model, max_input_tokens=max_input_tokens,
                               max_output_tokens=max_output_tokens, call=call, household_id=household_id)
    return paid_call


def _explain_one(paid_call, explainer, food_id, name, records, call_prefix):
    input_text = build_input(name or food_id, records)
    started = time.monotonic()
    try:
        text = paid_call(name=f'{call_prefix}:{food_id}', purpose='knowledge_explanation', provider='openai',
                         model=explainer.model, max_input_tokens=len((INSTRUCTIONS + input_text).encode()) + 400,
                         max_output_tokens=MAX_OUTPUT_TOKENS, call=lambda: explainer.respond(input_text))
    except spend.CallNotBilled:
        return None, {'ingredient_id': food_id, 'reason': 'EXPLANATION_UNAVAILABLE'}
    except tuple(BILLING_STOPS) as exc:
        return None, {'ingredient_id': food_id, 'reason': BILLING_STOPS[type(exc)]}
    try:
        result = validate(parse_output(text), records) if text else validate(None, records)
    except (ValueError, TypeError):
        result = {'status': 'rejected', 'failures': ['INVALID_FORMAT']}
    if result['status'] != 'accepted':
        return None, {'ingredient_id': food_id, 'reason': ','.join(result['failures'])}
    cited = {r['knowledge_id']: r for r in records}
    return {'ingredient_id': food_id, 'ingredient_name': name, 'tip_zh': result['tip_zh'],
            'prompt_version': PROMPT_VERSION, 'model': explainer.model,
            'latency_ms': int((time.monotonic() - started) * 1000),
            'citations': [{k: cited[c].get(k) for k in ('knowledge_id', 'source_version', 'ingredient_id',
                                                        'source_url', 'conditions')}
                          for c in result['citations']]}, None


def explain_snapshot(paid_call, explainer, snapshot: dict, names: dict | None = None, *,
                     call_prefix: str = 'explain', max_parallel: int = MAX_PARALLEL) -> dict:
    """snapshot：T11 retrieve_knowledge 的結果；names：{食材ID: 名稱}（缺時用知識紀錄的 ingredient_name）。"""
    names = dict(names or {})
    not_found = sorted(i for i, s in (snapshot.get('by_ingredient') or {}).items() if s == 'not_found')
    if snapshot.get('status') == 'unavailable':
        return {'status': 'unavailable', 'reason': snapshot.get('reason'), 'items': [], 'dropped': [], 'not_found': []}
    if explainer is None:
        return {'status': 'unavailable', 'reason': 'EXPLANATION_NOT_CONFIGURED', 'items': [], 'dropped': [],
                'not_found': not_found}
    by_food = {}
    for r in snapshot.get('records') or []:
        by_food.setdefault(r['ingredient_id'], []).append(r)
        names.setdefault(r['ingredient_id'], r.get('ingredient_name'))
    jobs = sorted(by_food.items())
    with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(jobs) or 1))) as pool:
        results = list(pool.map(lambda job: _explain_one(paid_call, explainer, job[0], names.get(job[0]), job[1],
                                                         call_prefix), jobs))
    items = [item for item, _ in results if item]
    dropped = [drop for _, drop in results if drop]
    status = 'available' if items else ('unavailable' if dropped and all(
        d['reason'] in BILLING_STOPS.values() or d['reason'] == 'EXPLANATION_UNAVAILABLE' for d in dropped)
        else 'not_found')
    return {'status': status, 'items': items, 'dropped': dropped, 'not_found': not_found}


def tips_for_menu(factory, *, final_food_ids: list[str], household_id, request_tag: str,
                  client=None, embedder=None, explainer=None, names: dict | None = None) -> dict:
    """同步 API 的整段流程：檢索 → 雲端生成。client／embedder／explainer 省略時依設定建立。"""
    from app.core.config import settings
    from app.services import knowledge
    food_ids = sorted(set(final_food_ids))
    if client is None and settings.knowledge_service_url and settings.knowledge_service_token:
        client = knowledge.HttpKnowledgeClient(settings.knowledge_service_url, settings.knowledge_service_token)
    if embedder is None and settings.knowledge_provider == 'openai' and settings.openai_api_key:
        embedder = knowledge.OpenAIEmbedder(settings.openai_api_key)
    explainer = explainer if explainer is not None else default_explainer()
    paid_call = request_paid_call(factory, request_tag=request_tag, household_id=household_id)
    query = '、'.join((names or {}).get(i) or i for i in food_ids) + ' 的營養與健康效益'
    try:
        snapshot = knowledge.retrieve_knowledge(final_food_ids=food_ids, query_text=query, client=client,
                                                embedder=embedder, paid_call=paid_call)
    except tuple(BILLING_STOPS) as exc:
        snapshot = knowledge.unavailable(food_ids, BILLING_STOPS[type(exc)])
    result = explain_snapshot(paid_call, explainer, snapshot, names)
    return {**result, 'final_food_ids': food_ids, 'knowledge_status': snapshot.get('status'),
            'index_version': snapshot.get('index_version')}
