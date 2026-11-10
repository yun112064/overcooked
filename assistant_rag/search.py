"""命令列試查：同一題比較六種檢索方法（11 組）。

  python -m assistant_rag.search "吃地瓜有什麼好處"                       # 11 組全部
  python -m assistant_rag.search "痛風可以吃豆腐嗎" --method entity_boost --embedder bge-m3
  python -m assistant_rag.search "…" --json                                 # 完整結果（含片段原文）
  python -m assistant_rag.search --interactive                              # 連續輸入問題

問題文字只在本機處理，不寫入日誌或資料庫。
"""
import argparse
import json
import sys

from assistant_rag import store
from assistant_rag.providers import EMBEDDERS, ProviderUnavailable
from assistant_rag.retrieval import METHODS, Retriever, combos


def selected(method: str, emb: str) -> list[tuple[str, str | None]]:
    out = []
    for m, e in combos():
        if method in ("all", m) and (e is None or emb in ("all", e)):
            out.append((m, e))
    if not out:
        raise SystemExit(f"沒有符合的組合：--method {method} --embedder {emb}")
    return out


def render(result, *, text: bool = False) -> str:
    extras = []
    for key in ("rewrites", "entities", "rewrite_error"):
        if key in result.extras:
            extras.append(f"{key}={result.extras[key]}")
    lines = [f"■ {result.label}  {result.timings.get('total', 0):.2f}s" + (f"  ({'; '.join(map(str, extras))})"
                                                                         if extras else "")]
    for h in result.hits:
        heading = " > ".join(h.heading_path)
        lines.append(f"  {h.rank}. {h.score:>8.4f}  [{h.source_name}] {h.title}" + (f" ｜{heading}" if heading else ""))
        if text:
            lines.append("       " + h.text.replace("\n", " ")[:160])
    return "\n".join(lines)


def run(retriever: Retriever, query: str, pairs, *, as_json: bool, text: bool) -> None:
    results = []
    for method, emb in pairs:
        try:
            results.append(retriever.search(query, method, emb))
        except ProviderUnavailable as exc:
            print(f"■ {method}@{emb}  不可用：{exc.code}", file=sys.stderr)
            continue
        if not as_json:
            print(render(results[-1], text=text), flush=True)
    if as_json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_rag.search", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="?")
    parser.add_argument("--method", default="all", choices=["all", *METHODS])
    parser.add_argument("--embedder", default="all", choices=["all", *EMBEDDERS])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true", help="顯示片段開頭")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)
    if not args.query and not args.interactive:
        parser.error("請輸入問題，或使用 --interactive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pairs = selected(args.method, args.embedder)
    with store.connect() as conn:
        retriever = Retriever(conn)
        if args.query:
            run(retriever, args.query, pairs, as_json=args.json, text=args.text)
        while args.interactive:
            try:
                query = input("\n問題（空白結束）：").strip()
            except EOFError:
                break
            if not query:
                break
            run(retriever, query, pairs, as_json=args.json, text=args.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
