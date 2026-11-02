"""T11 最後小知識文字：雲端 OpenAI 與地端 Ollama（qwen3.5:9b-q4_K_M）比較。資料都用 Excel 匯入的 123 筆審核知識。

  # 雲端（OPENAI_API_KEY）
  python scripts/t11_explain_benchmark.py run --provider openai --out outputs/t11_explain/cloud.jsonl
  # 地端（團隊電腦 Ollama）
  python scripts/t11_explain_benchmark.py run --provider ollama --model qwen3.5:9b-q4_K_M --out outputs/t11_explain/local.jsonl
  # 比較
  python scripts/t11_explain_benchmark.py compare --run 雲端=outputs/t11_explain/cloud.jsonl \\
      --run 地端=outputs/t11_explain/local.jsonl --out outputs/t11_explain/report

每種食材一次呼叫（76 種；每種最多 3 筆知識，等同 T11 檢索 limit 3 的結果），兩邊同一份提示詞與 JSON schema。
記錄每次延遲、token、費用與程式驗證結果；中斷後重跑會略過已完成的食材。地端第一個請求含模型載入，另計冷啟動。
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_rag.explain import (INSTRUCTIONS, PROMPT_VERSION, SCHEMA, build_input, ingredient_groups,  # noqa: E402
                                   parse_output, validate)

CANDIDATES = ROOT / 'knowledge_rag/candidates/web_paper_2026-10-30.json'
USD_TWD = Decimal('33')


def post(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 — 固定服務網址
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b'{}')
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {'error': {'code': type(exc).__name__}}


class OpenAI:
    name = 'openai'

    def __init__(self, model, key):
        if not key:
            raise SystemExit('請先設定 OPENAI_API_KEY')
        self.model, self.key = model, key

    def call(self, input_text):
        status, body = post('https://api.openai.com/v1/responses', {
            'model': self.model, 'instructions': INSTRUCTIONS, 'input': input_text, 'store': False,
            'max_output_tokens': 600,
            'text': {'format': {'type': 'json_schema', 'name': 't11_tip', 'schema': SCHEMA, 'strict': True}}},
            {'Authorization': 'Bearer ' + self.key}, 120)
        if status != 200:
            return None, None, f'HTTP_{status}:{((body or {}).get("error") or {}).get("code") or ""}'
        usage = {'input_tokens': body['usage']['input_tokens'], 'output_tokens': body['usage']['output_tokens']}
        content = [c for i in body.get('output') or [] if i.get('type') == 'message' for c in i.get('content') or []]
        if body.get('status') != 'completed' or any(c.get('type') == 'refusal' for c in content):
            return None, usage, f'STATUS_{body.get("status")}'
        return ''.join(c.get('text') or '' for c in content if c.get('type') == 'output_text'), usage, None


class Ollama:
    name = 'ollama'

    def __init__(self, model, url, think):
        self.model, self.url, self.think = model, url.rstrip('/'), think

    def call(self, input_text):
        payload = {'model': self.model, 'stream': False, 'format': SCHEMA, 'keep_alive': '10m',
                   'options': {'temperature': 0, 'num_ctx': 4096},
                   'messages': [{'role': 'system', 'content': INSTRUCTIONS}, {'role': 'user', 'content': input_text}]}
        if self.think is not None:
            payload['think'] = self.think
        status, body = post(self.url + '/api/chat', payload, {}, 600)
        if status != 200 or not isinstance(body.get('message'), dict):
            return None, None, f'HTTP_{status}:{(body or {}).get("error") or ""}'
        usage = {'input_tokens': int(body.get('prompt_eval_count') or 0), 'output_tokens': int(body.get('eval_count') or 0)}
        return body['message'].get('content'), usage, None


def cost(model, usage):
    models = json.loads((ROOT / 'config/model_pricing.json').read_text(encoding='utf-8'))['models']
    entry = models.get(model)
    if entry is None or usage is None:
        return None
    return str(((Decimal(entry['input']) * usage['input_tokens'] + Decimal(entry['output']) * usage['output_tokens'])
                / Decimal(1_000_000)).quantize(Decimal('0.000001')))


def cmd_run(args):
    records = json.loads(CANDIDATES.read_text(encoding='utf-8'))['records']
    groups = ingredient_groups(records)
    if args.limit:
        groups = groups[:args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)['ingredient_id'] for line in out.read_text(encoding='utf-8').splitlines() if line.strip()}
    if args.provider == 'openai':
        provider = OpenAI(args.model or 'gpt-5.4-mini-2026-03-17', os.environ.get('OPENAI_API_KEY', ''))
    else:
        think = None if args.think == 'default' else args.think == 'on'
        provider = Ollama(args.model or 'qwen3.5:9b-q4_K_M', args.ollama_url, think)
    started = time.monotonic()
    todo = [g for g in groups if g[0] not in done]
    for n, (food_id, name, recs) in enumerate(todo, 1):
        input_text = build_input(name, recs)
        t0 = time.perf_counter()
        text, usage, error = provider.call(input_text)
        ms = round((time.perf_counter() - t0) * 1000)
        output = None
        if text:
            try:
                output = parse_output(text)
            except (ValueError, TypeError):
                error = error or 'INVALID_FORMAT'
        v = validate(output, recs)
        row = dict(ingredient_id=food_id, ingredient_name=name, knowledge_ids=[r['knowledge_id'] for r in recs],
                   manual=[r['fact_zh'] for r in recs], provider=provider.name, model=provider.model,
                   prompt_version=PROMPT_VERSION, latency_ms=ms, first_call=(n == 1 and not done), usage=usage,
                   cost_usd=cost(provider.model, usage), error=error, raw=None if output else text, output=output,
                   validation=v)
        with out.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'[{n}/{len(todo)}] {name} {v["status"]} {ms}ms {error or ""}', flush=True)
    print(json.dumps(dict(provider=provider.name, model=provider.model, done=len(todo),
                          wall_seconds=round(time.monotonic() - started, 1)), ensure_ascii=False))


def pct(values, q):
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo))


def summarize(rows):
    warm = [r['latency_ms'] for r in rows if not r.get('first_call') and not r.get('error')]
    cold = [r['latency_ms'] for r in rows if r.get('first_call')]
    usage_in = sum((r.get('usage') or {}).get('input_tokens', 0) for r in rows)
    usage_out = sum((r.get('usage') or {}).get('output_tokens', 0) for r in rows)
    costs = [Decimal(r['cost_usd']) for r in rows if r.get('cost_usd')]
    fails = Counter(f for r in rows for f in r['validation']['failures'])
    total = sum(costs, Decimal(0))
    return dict(model=rows[0]['model'] if rows else '', n=len(rows),
                accepted=sum(r['validation']['status'] == 'accepted' for r in rows),
                errors=sum(bool(r.get('error')) for r in rows), failures=dict(fails.most_common()),
                cold_ms=cold[0] if cold else None,
                mean_ms=round(statistics.mean(warm)) if warm else None, p50_ms=pct(warm, .5), p95_ms=pct(warm, .95),
                max_ms=max(warm) if warm else None, total_s=round(sum(r['latency_ms'] for r in rows) / 1000, 1),
                tokens_in=usage_in, tokens_out=usage_out,
                cost_usd=str(total.quantize(Decimal('0.0001'))) if costs else '0',
                cost_twd=str((total * USD_TWD).quantize(Decimal('0.01'))) if costs else '0',
                avg_chars=round(statistics.mean(len(r['validation']['tip_zh'] or '') for r in rows if r['validation']['tip_zh']), 1)
                if any(r['validation']['tip_zh'] for r in rows) else None)


def cmd_compare(args):
    runs = []
    for spec in args.run:
        name, _, path = spec.partition('=')
        rows = {json.loads(l)['ingredient_id']: json.loads(l) for l in Path(path).read_text(encoding='utf-8').splitlines() if l.strip()}
        runs.append((name, rows))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sums = [(name, summarize(list(rows.values()))) for name, rows in runs]
    lines = ['# T11 最後小知識文字：雲端與地端比較', '',
             f'資料：Excel 匯入的 123 筆審核知識（76 種食材，每種一次呼叫）；提示詞 `{PROMPT_VERSION}`，兩邊相同。', '',
             '| 項目 | ' + ' | '.join(n for n, _ in sums) + ' |', '|---|' + '---|' * len(sums)]

    def row(label, fn):
        lines.append(f'| {label} | ' + ' | '.join(str(fn(s)) for _, s in sums) + ' |')
    row('模型', lambda s: s['model'])
    row('通過程式驗證', lambda s: f"{s['accepted']}/{s['n']}")
    row('呼叫錯誤', lambda s: s['errors'])
    row('冷啟動第一次（ms）', lambda s: s['cold_ms'])
    row('每次 平均／p50／p95（ms）', lambda s: f"{s['mean_ms']}／{s['p50_ms']}／{s['p95_ms']}")
    row('最慢一次（ms）', lambda s: s['max_ms'])
    row('76 種全部（秒）', lambda s: s['total_s'])
    row('平均字數', lambda s: s['avg_chars'])
    row('Token 輸入／輸出', lambda s: f"{s['tokens_in']:,}／{s['tokens_out']:,}")
    row('費用 USD／TWD', lambda s: f"{s['cost_usd']}／{s['cost_twd']}")
    lines += ['', '驗證未通過原因：']
    for name, s in sums:
        lines.append(f"- {name}：" + ('、'.join(f'{k} {v}' for k, v in s['failures'].items()) or '無'))
    lines += ['', '驗證規則：引用的知識 ID 必須是本次提供的；文字中的營養／效益關鍵字與數字都要出自所引用知識；'
              '不得有治療、保證等誇大用詞；一般衛教文章不可寫「研究指出」。程式檢查是必要條件，請在 comparison.csv 人工判定。']
    (out / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (out / 'summary.json').write_text(json.dumps(dict(sums), ensure_ascii=False, indent=1), encoding='utf-8')
    ids = sorted(set().union(*(rows.keys() for _, rows in runs)))
    with (out / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['食材ID', '食材', '資料庫知識（Excel fact_zh）'] + [x for name, _ in runs for x in (
            f'{name}小知識', f'{name}驗證', f'{name}未通過原因', f'{name}ms', f'{name}人工判定')])
        for i in ids:
            first = next(rows[i] for _, rows in runs if i in rows)
            line = [i, first['ingredient_name'], '\n'.join(first['manual'])]
            for _, rows in runs:
                r = rows.get(i)
                line += ([r['validation']['tip_zh'] or '', '通過' if r['validation']['status'] == 'accepted' else '未通過',
                          ','.join(r['validation']['failures']), r['latency_ms'], ''] if r else ['', '未執行', '', '', ''])
            w.writerow(line)
    print('\n'.join(lines))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--provider', choices=['openai', 'ollama'], required=True)
    r.add_argument('--model')
    r.add_argument('--out', required=True)
    r.add_argument('--limit', type=int)
    r.add_argument('--ollama-url', default=os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434'))
    r.add_argument('--think', choices=['default', 'on', 'off'], default='off',
                   help='Qwen3 系列的思考模式；預設關閉（與雲端一樣直接回答）')
    c = sub.add_parser('compare')
    c.add_argument('--run', action='append', required=True)
    c.add_argument('--out', default='outputs/t11_explain/report')
    args = p.parse_args(argv)
    (cmd_run if args.cmd == 'run' else cmd_compare)(args)


if __name__ == '__main__':
    main()
